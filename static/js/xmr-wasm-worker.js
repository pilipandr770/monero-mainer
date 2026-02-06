// Placeholder worker for xmr-wasm integration.
// In production this should load the actual xmrig-wasm script and expose a message API:
// - {type: 'init', opts} -> initializes miner (pool, wallet, threads, throttle)
// - {type: 'start'} / {type: 'stop'}
// - {type: 'stats'} -> posts back stats

self.onmessage = function(e) {
    const data = e.data || {};
    if (data.type === 'init') {
        // This worker expects that you will supply a real WASM runtime here.
        // For now, we return 'not supported'. Replace with xmrig-wasm bootstrap.
        postMessage({ type: 'error', error: 'xmr-wasm not implemented in placeholder worker. Place real worker implementation here.' });
    } else if (data.type === 'stop') {
        postMessage({ type: 'stopped' });
    } else if (data.type === 'stats') {
        postMessage({ type: 'stats', hashrate: 0, totalHashes: 0, acceptedShares: 0 });
    }
};
