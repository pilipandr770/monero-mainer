"""
WebSocket ↔ Stratum TCP proxy for browser mining.
Browser connects via WebSocket, proxy relays JSON-RPC to mining pool over TCP.
"""
import json
import socket
import threading
import time
import logging

logger = logging.getLogger(__name__)


class StratumProxy:
    """Single pool connection shared by all browser miners."""

    def __init__(self, pool_host, pool_port, wallet, worker_prefix='web', password='x'):
        self.pool_host = pool_host
        self.pool_port = pool_port
        self.wallet = wallet
        self.worker_prefix = worker_prefix
        self.password = password

        self.sock = None
        self.connected = False
        self.job = None           # current mining job from pool
        self.job_id = None
        self.target = None
        self.req_id = 1
        self.lock = threading.Lock()
        self.listeners = []       # list of WebSocket send callbacks
        self._recv_thread = None
        self._buffer = ''

    def connect(self):
        """Connect to pool and send login."""
        if self.connected:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(30)
            self.sock.connect((self.pool_host, self.pool_port))
            self.connected = True
            logger.info(f"Connected to pool {self.pool_host}:{self.pool_port}")

            # Start receiver thread
            self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self._recv_thread.start()

            # Login
            self._send_to_pool({
                "id": self._next_id(),
                "method": "login",
                "params": {
                    "login": self.wallet,
                    "pass": self.password,
                    "agent": "MineWithMe/1.0",
                    "algo": ["cn/r", "cn/0", "cn/1", "cn/2", "cn-lite/1", "rx/0"]
                }
            })
            return True
        except Exception as e:
            logger.error(f"Pool connection failed: {e}")
            self.connected = False
            return False

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
        """Read from pool socket and broadcast to all connected browsers."""
        while self.connected:
            try:
                data = self.sock.recv(4096)
                if not data:
                    logger.warning("Pool connection closed")
                    self.connected = False
                    break

                self._buffer += data.decode('utf-8', errors='replace')
                while '\n' in self._buffer:
                    line, self._buffer = self._buffer.split('\n', 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        self._handle_pool_message(msg)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from pool: {line[:100]}")

            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"Pool receive error: {e}")
                self.connected = False
                break

    def _handle_pool_message(self, msg):
        """Process pool message and relay to browsers."""
        # Login response
        if 'result' in msg and 'job' in msg.get('result', {}):
            result = msg['result']
            self.job_id = result.get('id')
            self.job = result['job']
            self.target = self.job.get('target')
            logger.info(f"Logged in to pool, job received: {self.job.get('job_id', 'unknown')}")

        # New job notification
        if msg.get('method') == 'job':
            self.job = msg.get('params', {})
            self.target = self.job.get('target')
            logger.info(f"New job from pool: {self.job.get('job_id', 'unknown')}")

        # Broadcast to all connected browsers
        broadcast_msg = json.dumps(msg)
        dead = []
        for i, send_fn in enumerate(self.listeners):
            try:
                send_fn(broadcast_msg)
            except Exception:
                dead.append(i)
        # Remove dead listeners
        for i in reversed(dead):
            self.listeners.pop(i)

    def submit_share(self, nonce, result_hash, job_id=None):
        """Submit a found share to the pool."""
        if not self.connected:
            return False
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
        return self._send_to_pool(submit)

    def add_listener(self, send_fn):
        """Register a WebSocket callback to receive pool messages."""
        self.listeners.append(send_fn)
        # If we already have a job, send it immediately
        if self.job:
            try:
                send_fn(json.dumps({"method": "job", "params": self.job}))
            except Exception:
                pass

    def remove_listener(self, send_fn):
        """Unregister a WebSocket callback."""
        try:
            self.listeners.remove(send_fn)
        except ValueError:
            pass

    def disconnect(self):
        """Close pool connection."""
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# Global proxy instance (lazy init)
_proxy = None
_proxy_lock = threading.Lock()


def get_proxy(pool_url, wallet):
    """Get or create shared StratumProxy instance."""
    global _proxy
    with _proxy_lock:
        if _proxy and _proxy.connected:
            return _proxy

        # Parse pool_url (host:port)
        parts = pool_url.split(':')
        host = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 10004

        _proxy = StratumProxy(host, port, wallet)
        if _proxy.connect():
            return _proxy
        else:
            _proxy = None
            return None
