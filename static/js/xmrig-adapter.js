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
        this.hashrate = 0;
        this.totalHashes = 0;
        this.acceptedShares = 0;
        this.currentJob = null;
    }

    async start(opts) {
        this.threads = opts.threads || navigator.hardwareConcurrency || 2;
        this.running = true;

        // Connect WebSocket to Flask Stratum proxy
        const wsProtocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${location.host}/ws/mining`;

        return new Promise((resolve, reject) => {
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('✅ WebSocket connected to Stratum proxy');
                // Request current job
                this.ws.send(JSON.stringify({ type: 'get_job' }));
                // Start workers
                this._startWorkers();
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
                reject(new Error('WebSocket connection failed'));
            };

            this.ws.onclose = () => {
                console.log('WebSocket closed');
                if (this.running) {
                    // Auto-reconnect after 5s
                    setTimeout(() => {
                        if (this.running) {
                            console.log('Reconnecting to pool...');
                            this.start(opts).catch(console.error);
                        }
                    }, 5000);
                }
            };
        });
    }

    _startWorkers() {
        for (let i = 0; i < this.threads; i++) {
            const worker = new Worker('/static/js/xmr-wasm-worker.js');

            worker.onmessage = (e) => {
                const data = e.data;
                if (data.type === 'ready') {
                    console.log(`Worker ${i} ready`);
                    // Send current job if available
                    if (this.currentJob) {
                        worker.postMessage({ type: 'job', job: this.currentJob });
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
                    }
                    this.acceptedShares++;
                } else if (data.type === 'stats') {
                    this.hashrate = data.hashrate || 0;
                    this.totalHashes += (data.totalHashes - this.totalHashes > 0) ? data.totalHashes - this.totalHashes : 0;
                } else if (data.type === 'error') {
                    console.error(`Worker ${i} error:`, data.error);
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
            console.log('📋 New job:', this.currentJob.job_id);
            // Forward to all workers
            this.workers.forEach(w => {
                w.postMessage({ type: 'job', job: this.currentJob });
            });
        }
        // Submit acknowledgement
        if (msg.type === 'submit_ack') {
            console.log('✓ Share submission:', msg.success ? 'accepted' : 'rejected');
        }
        // Login result with job
        if (msg.result && msg.result.job) {
            this.currentJob = msg.result.job;
            console.log('📋 Initial job:', this.currentJob.job_id);
            this.workers.forEach(w => {
                w.postMessage({ type: 'job', job: this.currentJob });
            });
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