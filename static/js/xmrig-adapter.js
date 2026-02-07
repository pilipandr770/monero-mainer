/**
 * Mining Adapter - bridges WASM CryptoNight workers with WebSocket Stratum proxy.
 * Checks for WASM availability, creates workers, manages WebSocket to Flask proxy.
 */

(async function(){
    window.RealWasmAvailable = false;
    window.RealMiner = null;

    try {
        // Check if WASM files exist
        const [wasmResp, jsResp] = await Promise.all([
            fetch('/static/wasm/cryptonight.wasm', { method: 'HEAD' }),
            fetch('/static/wasm/cryptonight.js', { method: 'HEAD' })
        ]);

        if (wasmResp.ok && jsResp.ok) {
            window.RealWasmAvailable = true;
            console.log('✅ CryptoNight WASM found: Real mining available');

            window.RealMiner = new RealWasmMiner();
        } else {
            console.log('ℹ️ WASM not present; demo mode will be used');
        }
    } catch (e) {
        console.warn('ℹ️ Error checking for WASM files:', e);
    }
})();


class RealWasmMiner {
    constructor() {
        this.workers = [];
        this.ws = null;
        this.running = false;
        this.threads = 1;
        this.workerHashrates = {};  // per-worker hashrate tracking
        this.hashrate = 0;
        this.totalHashes = 0;
        this.acceptedShares = 0;
        this.currentJob = null;
        this._reconnecting = false;
        this.userWallet = '';  // user's XMR wallet for 85% rewards
    }

    async start(opts) {
        this.threads = opts.threads || navigator.hardwareConcurrency || 2;
        this.running = true;
        this.userWallet = opts.userWallet || '';

        // Start workers only if none exist (don't destroy workers on short reconnects)
        if (this.workers.length === 0) {
            // No workers yet — will create them when WS opens
        } else {
            // Reuse existing workers on reconnect to avoid losing state and having no workers ready when job arrives
            console.log('Reusing existing workers on reconnect');
        }

        // Connect WebSocket to Flask Stratum proxy
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${location.host}/ws/mining`;

        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('✅ WebSocket connected to Stratum proxy');
                this._reconnecting = false;
                // Send user wallet to server for fee splitting
                this.ws.send(JSON.stringify({ 
                    type: 'set_wallet', 
                    wallet: this.userWallet 
                }));
                // Request current job
                this.ws.send(JSON.stringify({ type: 'get_job' }));
                // Start workers if we don't have them already
                if (this.workers.length === 0) {
                    this._startWorkers();
                } else {
                    // If workers exist and we already have a job cached, forward it
                    if (this.currentJob) {
                        this.workers.forEach((w, idx) => {
                            try { w.postMessage({ type: 'job', job: this.currentJob, workerId: idx, totalWorkers: this.threads }); } catch(e) {}
                        });
                    }
                }
                resolve();
            };

            this.ws.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    this._handlePoolMessage(msg);
                } catch (err) {
                    console.warn('Invalid message from proxy:', err);
                }
            };

            this.ws.onerror = (e) => {
                console.error('WebSocket error:', e);
                if (!this._reconnecting) {
                    reject(new Error('WebSocket connection failed'));
                }
            };

            this.ws.onclose = () => {
                console.log('WebSocket closed');
                if (this.running && !this._reconnecting) {
                    this._reconnecting = true;
                    // Auto-reconnect after 5s
                    setTimeout(() => {
                        if (this.running) {
                            console.log('Reconnecting to pool...');
                            this.start(opts).catch(err => {
                                console.error('Reconnect failed:', err);
                                // Retry again
                                this._reconnecting = false;
                            });
                        }
                    }, 5000);
                }
            };
        });
    }

    _startWorkers() {
        for (let i = 0; i < this.threads; i++) {
            const worker = new Worker('/static/js/xmr-wasm-worker.js');
            const workerId = i;

            worker.onmessage = (e) => {
                const data = e.data;
                if (data.type === 'ready') {
                    console.log(`Worker ${workerId} ready`);
                    // Send current job if available
                    if (this.currentJob) {
                        worker.postMessage({ type: 'job', job: this.currentJob, workerId: workerId, totalWorkers: this.threads });
                    }
                } else if (data.type === 'share') {
                    // Forward share to pool via WebSocket
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(JSON.stringify({
                            type: 'submit',
                            nonce: data.nonce,
                            result: data.result,
                            job_id: data.job_id
                        }));
                        console.log(`⛏️ Share from worker ${workerId}: nonce=${data.nonce}`);
                    }
                    this.acceptedShares++;
                } else if (data.type === 'stats') {
                    // Aggregate hashrate from all workers
                    this.workerHashrates[workerId] = data.hashrate || 0;
                    let total = 0;
                    for (const key in this.workerHashrates) {
                        total += this.workerHashrates[key];
                    }
                    this.hashrate = total;
                    this.totalHashes += data.batchHashes || 0;
                } else if (data.type === 'error') {
                    console.error(`Worker ${workerId} error:`, data.error);
                }
            };

            worker.postMessage({ type: 'init' });
            this.workers.push(worker);
        }
    }

    _handlePoolMessage(msg) {
        // New job from pool
        if (msg.method === 'job' && msg.params) {
            this.currentJob = msg.params;
            console.log('📋 New job:', this.currentJob.job_id, 'target:', this.currentJob.target);
            // Forward to all workers
            this.workers.forEach((w, idx) => {
                w.postMessage({ type: 'job', job: this.currentJob, workerId: idx, totalWorkers: this.threads });
            });
        }
        // Submit acknowledgement
        if (msg.type === 'submit_ack') {
            console.log('✓ Share submission:', msg.success ? 'accepted' : 'rejected');
        }
        // Wallet acknowledgement
        if (msg.type === 'wallet_ack') {
            console.log('💰 Wallet ack:', msg.message);
        }
        // Wallet switch notification (85/15 timer)
        if (msg.type === 'wallet_switch') {
            const modeEl = document.getElementById('walletInfoMode');
            if (modeEl) {
                if (msg.wallet_type === 'user') {
                    modeEl.textContent = '⛏️ Сейчас: майнинг на ТВОЙ кошелёк (85%)';
                    modeEl.className = 'text-green-300 mt-1 font-semibold';
                } else {
                    modeEl.textContent = '🔧 Сейчас: dev fee (15%)';
                    modeEl.className = 'text-yellow-300 mt-1 font-semibold';
                }
            }
            console.log(`🔄 Wallet switch: ${msg.wallet_type} — ${msg.message}`);
        }
        // Login result with job
        if (msg.result && typeof msg.result === 'object' && msg.result.job) {
            this.currentJob = msg.result.job;
            console.log('📋 Initial job:', this.currentJob.job_id, 'target:', this.currentJob.target);
            this.workers.forEach((w, idx) => {
                w.postMessage({ type: 'job', job: this.currentJob, workerId: idx, totalWorkers: this.threads });
            });
        }
        // Pool error
        if (msg.error) {
            console.error('Pool error:', msg.error);
        }
    }

    stop() {
        this.running = false;
        this.workers.forEach(w => {
            w.postMessage({ type: 'stop' });
            w.terminate();
        });
        this.workers = [];
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    getHashrate() { return this.hashrate; }
    getTotalHashes() { return this.totalHashes; }
    getAcceptedShares() { return this.acceptedShares; }
    getStats() {
        return {
            hashrate: this.hashrate,
            totalHashes: this.totalHashes,
            acceptedShares: this.acceptedShares
        };
    }
}