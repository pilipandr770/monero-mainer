"""
WebSocket ↔ Stratum TCP proxy for browser mining.
Per-session proxy: each browser gets its own pool connection.
Supports time-based dev fee: 85% user wallet, 15% dev wallet.
"""
import json
import socket
import threading
import time
import logging

logger = logging.getLogger(__name__)


class StratumSession:
    """
    Per-browser-session pool connection with time-based wallet switching.
    85% of time → user's wallet, 15% → dev wallet.
    If no user wallet provided, 100% goes to dev wallet.
    """

    CYCLE_SECONDS = 100       # full cycle: 85s user + 15s dev
    USER_FRACTION = 0.85      # 85% for user

    def __init__(self, pool_host, pool_port, dev_wallet, user_wallet=None, password='x'):
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.dev_wallet = dev_wallet
        self.user_wallet = user_wallet or ''
        self.password = password

        self.sock = None
        self.connected = False
        self.job = None
        self.job_id = None
        self.target = None
        self.req_id = 1
        self.lock = threading.Lock()
        self._send_fn = None     # single WebSocket send callback
        self._recv_thread = None
        self._switch_thread = None
        self._buffer = ''
        self._last_share_time = 0
        self._share_interval = 2.0
        self._shares_submitted = 0
        self._shares_accepted = 0
        self._current_wallet = None   # which wallet is currently logged in
        self._stop_event = threading.Event()

    @property
    def has_user_wallet(self):
        return bool(self.user_wallet and len(self.user_wallet) >= 90)

    @property
    def active_wallet(self):
        return self._current_wallet or self.dev_wallet

    def set_user_wallet(self, wallet):
        """Set user wallet (called when browser sends set_wallet message)."""
        if wallet and len(wallet) >= 90 and (wallet.startswith('4') or wallet.startswith('8')):
            self.user_wallet = wallet
            logger.info(f"User wallet set: {wallet[:12]}...")
            # If already connected, start wallet switching
            if self.connected and not self._switch_thread:
                self._start_wallet_switching()
        else:
            self.user_wallet = ''
            logger.info("No valid user wallet — 100% dev mode")

    def connect(self):
        """Connect to pool and login with initial wallet."""
        if self.connected:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(30)
            logger.info(f"Session connecting to pool {self.pool_host}:{self.pool_port}...")
            self.sock.connect((self.pool_host, self.pool_port))
            self.connected = True
            self._buffer = ''
            self._stop_event.clear()

            # Start receiver thread
            self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._recv_thread.start()

            # Initial login: if user wallet exists, start with user wallet (85%)
            time.sleep(0.1)
            initial_wallet = self.user_wallet if self.has_user_wallet else self.dev_wallet
            self._login(initial_wallet)

            # Start wallet switching if user wallet exists
            if self.has_user_wallet:
                self._start_wallet_switching()

            return True
        except Exception as e:
            logger.error(f"Session pool connection failed: {e}", exc_info=True)
            self.connected = False
            return False

    def _login(self, wallet):
        """Send login message to pool with specified wallet."""
        self._current_wallet = wallet
        wallet_type = "USER" if wallet == self.user_wallet else "DEV"
        logger.info(f"Login to pool as {wallet_type}: {wallet[:12]}...")

        login_msg = {
            "id": self._next_id(),
            "method": "login",
            "params": {
                "login": wallet,
                "pass": self.password,
                "agent": "MineWithMe/1.0",
                "algo": ["cn/r", "cn/0", "cn/1", "cn/2", "cn-lite/1", "rx/0"]
            }
        }
        self._send_to_pool(login_msg)

    def _start_wallet_switching(self):
        """Start background thread that switches wallets on a timer."""
        if self._switch_thread and self._switch_thread.is_alive():
            return
        self._switch_thread = threading.Thread(target=self._wallet_switch_loop, daemon=True)
        self._switch_thread.start()
        logger.info("Wallet switching started (85% user / 15% dev)")

    def _wallet_switch_loop(self):
        """
        Cycle: 85 seconds → user wallet, 15 seconds → dev wallet.
        Re-login to pool switches which wallet receives the rewards.
        """
        user_time = int(self.CYCLE_SECONDS * self.USER_FRACTION)   # 85s
        dev_time = self.CYCLE_SECONDS - user_time                   # 15s

        while self.connected and not self._stop_event.is_set():
            # Phase 1: Mine for USER wallet (85s)
            if self._current_wallet != self.user_wallet:
                self._login(self.user_wallet)
                self._notify_wallet_switch("user")

            if self._stop_event.wait(timeout=user_time):
                break

            if not self.connected:
                break

            # Phase 2: Mine for DEV wallet (15s)
            if self._current_wallet != self.dev_wallet:
                self._login(self.dev_wallet)
                self._notify_wallet_switch("dev")

            if self._stop_event.wait(timeout=dev_time):
                break

        logger.info("Wallet switch loop ended")

    def _notify_wallet_switch(self, wallet_type):
        """Notify browser about wallet switch."""
        if self._send_fn:
            try:
                self._send_fn(json.dumps({
                    "type": "wallet_switch",
                    "wallet_type": wallet_type,
                    "message": f"Mining for {'your wallet' if wallet_type == 'user' else 'dev fee'}"
                }))
            except Exception:
                pass

    def reconnect(self):
        """Reconnect to pool after disconnection."""
        logger.info("Session attempting pool reconnection...")
        self.disconnect()
        time.sleep(2)
        return self.connect()

    def _next_id(self):
        with self.lock:
            self.req_id += 1
            return self.req_id

    def _send_to_pool(self, msg):
        """Send JSON-RPC message to pool."""
        if not self.connected or not self.sock:
            return False
        try:
            data = json.dumps(msg) + '\n'
            self.sock.sendall(data.encode())
            return True
        except Exception as e:
            logger.error(f"Send to pool failed: {e}")
            self.connected = False
            return False

    def _receive_loop(self):
        """Read from pool socket and forward to browser."""
        logger.info("Session receive loop started")
        while self.connected and not self._stop_event.is_set():
            try:
                data = self.sock.recv(4096)
                if not data:
                    logger.warning("Pool connection closed (empty recv)")
                    self.connected = False
                    break

                self._buffer += data.decode('utf-8', errors='replace')
                while self._buffer and '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        logger.info(f"Pool → session: {line[:300]}")
                        self._handle_pool_message(msg)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from pool: {line[:100]}")
                    except Exception as e:
                        logger.error(f"Error handling pool message: {e}", exc_info=True)

            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"Pool socket error: {e}", exc_info=True)
                self.connected = False
                break

        logger.info("Session receive loop ended")
        # Auto-reconnect
        if not self._stop_event.is_set() and not self.connected:
            threading.Thread(target=self._auto_reconnect, daemon=True).start()

    def _auto_reconnect(self):
        """Auto-reconnect to pool after disconnection."""
        for attempt in range(5):
            if self._stop_event.is_set():
                return
            time.sleep(5 * (attempt + 1))
            logger.info(f"Session auto-reconnect attempt {attempt + 1}/5...")
            if self.reconnect():
                logger.info("Session auto-reconnect successful!")
                return
        logger.error("Session auto-reconnect failed after 5 attempts")

    def _handle_pool_message(self, msg):
        """Process pool message and relay to browser."""
        # Error response from pool
        if msg.get('error'):
            logger.error(f"Pool error: {msg['error']}")

        # Login response
        result = msg.get('result')
        if isinstance(result, dict) and 'job' in result:
            self.job_id = result.get('id')
            self.job = result['job']
            self.target = self.job.get('target')
            wallet_type = "USER" if self._current_wallet == self.user_wallet else "DEV"
            logger.info(f"Logged in ({wallet_type}), job: {self.job.get('job_id', '?')}, target={self.target}")

        # Share accepted
        if isinstance(result, dict) and result.get('status') == 'OK':
            self._shares_accepted += 1
            wallet_type = "USER" if self._current_wallet == self.user_wallet else "DEV"
            logger.info(f"Share ACCEPTED ({wallet_type})! ({self._shares_accepted}/{self._shares_submitted})")

        # New job notification
        if msg.get('method') == 'job':
            self.job = msg.get('params', {})
            self.target = self.job.get('target')
            logger.info(f"New job: {self.job.get('job_id', '?')}, target={self.target}")

        # Forward to browser
        if self._send_fn:
            try:
                self._send_fn(json.dumps(msg))
            except Exception:
                pass

    def submit_share(self, nonce, result_hash, job_id=None):
        """Submit a found share to the pool (rate-limited)."""
        if not self.connected:
            logger.warning("Pool disconnected, attempting reconnect for share submission")
            if not self.reconnect():
                return False

        # Rate limit
        now = time.time()
        if now - self._last_share_time < self._share_interval:
            return False
        self._last_share_time = now

        submit = {
            "id": self._next_id(),
            "method": "submit",
            "params": {
                "id": self.job_id,
                "job_id": job_id or (self.job.get('job_id') if self.job else ''),
                "nonce": nonce,
                "result": result_hash
            }
        }
        self._shares_submitted += 1
        wallet_type = "USER" if self._current_wallet == self.user_wallet else "DEV"
        logger.info(f"Submitting share #{self._shares_submitted} ({wallet_type}): nonce={nonce[:8]}")
        return self._send_to_pool(submit)

    def set_listener(self, send_fn):
        """Set the WebSocket callback for this session."""
        self._send_fn = send_fn
        # Send cached job if available
        if self.job:
            try:
                send_fn(json.dumps({"method": "job", "params": self.job}))
            except Exception:
                pass

    def disconnect(self):
        """Close pool connection and stop threads."""
        self._stop_event.set()
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


def create_session(pool_url, dev_wallet, user_wallet=None):
    """Create a new per-browser StratumSession."""
    parts = pool_url.split(':')
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 10004

    session = StratumSession(host, port, dev_wallet, user_wallet)
    if session.connect():
        return session
    return None
