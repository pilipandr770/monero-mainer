// Adapter for xmrig-wasm
// Checks for presence of WASM and worker, exposes RealMiner interface if available.

(async function(){
    window.RealWasmAvailable = false;
    window.RealMiner = null;

    try {
        // Check if wasm file exists (head request)
        const resp = await fetch('/static/wasm/xmrig.wasm', { method: 'HEAD' });
        if (resp.ok) {
            window.RealWasmAvailable = true;
            console.log('✅ xmrig.wasm found: Real WASM mining is available');

            // Try to create worker wrapper
            try {
                const worker = new Worker('/static/js/xmr-wasm-worker.js');
                window.RealMiner = {
                    start: (opts) => new Promise((res, rej) => {
                        worker.postMessage({ type: 'init', opts });
                        worker.onmessage = (e) => {
                            if (e.data && e.data.type === 'ready') res();
                            if (e.data && e.data.type === 'error') rej(e.data.error);
                        };
                    }),
                    stop: () => worker.postMessage({ type: 'stop' }),
                    getStats: () => new Promise((res) => { worker.postMessage({ type: 'stats' }); worker.onmessage = (e) => res(e.data || {}); })
                };
            } catch (e) {
                console.warn('⚠️ Could not create xmrig worker, fallback to LocalMiner', e);
                window.RealWasmAvailable = false;
            }
        } else {
            console.log('ℹ️ xmrig.wasm not present; real WASM mining unavailable');
        }
    } catch (e) {
        console.warn('ℹ️ Error checking for xmrig.wasm, will use fallback miner', e);
    }
})();