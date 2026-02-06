/**
 * CryptoNight WASM Mining Worker
 * Loads CryptoNight WASM module, receives jobs from main thread,
 * computes hashes, and reports results back.
 */

let cn = null;       // CryptoNight WASM module
let cnHash = null;   // cwrap'd cn_hash function
let tryHash = null;  // cwrap'd try_hash function
let mining = false;
let currentJob = null;
let totalHashes = 0;
let hashrate = 0;
let acceptedShares = 0;

// Load WASM module
importScripts('/static/wasm/cryptonight.js');

async function initWasm() {
    try {
        cn = await CryptoNight();
        cnHash = cn.cwrap('cn_hash', null, ['number', 'number', 'number']);
        tryHash = cn.cwrap('try_hash', 'number', ['number', 'number', 'number', 'number', 'number']);
        postMessage({ type: 'ready' });
        console.log('[Worker] CryptoNight WASM initialized');
    } catch (e) {
        postMessage({ type: 'error', error: 'Failed to init WASM: ' + e.message });
    }
}

function hexToBytes(hex) {
    const bytes = [];
    for (let i = 0; i < hex.length; i += 2) {
        bytes.push(parseInt(hex.substr(i, 2), 16));
    }
    return new Uint8Array(bytes);
}

function bytesToHex(bytes) {
    return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}

function targetToUint64(targetHex) {
    // Pool sends target as 8-char hex (little-endian 32-bit) or longer
    // Convert to difficulty threshold
    if (targetHex.length <= 8) {
        // 4-byte target, convert to 64-bit threshold
        const t = parseInt(targetHex, 16);
        if (t === 0) return 0xFFFFFFFFFFFFFFFF;
        return Math.floor(0xFFFFFFFF / t) * 0x100000000;
    }
    return parseInt(targetHex, 16);
}

function mineLoop() {
    if (!mining || !currentJob || !cn) return;

    const blob = hexToBytes(currentJob.blob);
    const blobLen = blob.length;
    const target = targetToUint64(currentJob.target);

    // Allocate WASM memory
    const inputPtr = cn._malloc(blobLen);
    const outputPtr = cn._malloc(32);
    cn.HEAPU8.set(blob, inputPtr);

    const batchSize = 256;
    const startNonce = Math.floor(Math.random() * 0xFFFFFFFF);
    const startTime = performance.now();

    for (let i = 0; i < batchSize; i++) {
        if (!mining) break;

        const nonce = (startNonce + totalHashes + i) & 0xFFFFFFFF;

        // Set nonce in blob (offset 39, little-endian)
        cn.HEAPU8[inputPtr + 39] = nonce & 0xFF;
        cn.HEAPU8[inputPtr + 40] = (nonce >> 8) & 0xFF;
        cn.HEAPU8[inputPtr + 41] = (nonce >> 16) & 0xFF;
        cn.HEAPU8[inputPtr + 42] = (nonce >> 24) & 0xFF;

        // Compute CryptoNight hash
        cnHash(inputPtr, blobLen, outputPtr);

        // Check against target (last 8 bytes of hash)
        const hashBytes = new Uint8Array(cn.HEAPU8.buffer, outputPtr, 32);
        const hashLow = hashBytes[24] | (hashBytes[25] << 8) | (hashBytes[26] << 16) | (hashBytes[27] << 24);
        const hashHigh = hashBytes[28] | (hashBytes[29] << 8) | (hashBytes[30] << 16) | (hashBytes[31] << 24);
        const hashVal = hashHigh * 0x100000000 + (hashLow >>> 0);

        if (hashVal < target) {
            // Found valid share!
            const nonceHex = nonce.toString(16).padStart(8, '0');
            // Reverse nonce bytes for submission (little-endian hex)
            const nonceLe = nonceHex.match(/../g).reverse().join('');
            const resultHex = bytesToHex(hashBytes);

            postMessage({
                type: 'share',
                nonce: nonceLe,
                result: resultHex,
                job_id: currentJob.job_id
            });
            acceptedShares++;
        }
    }

    totalHashes += batchSize;
    const elapsed = (performance.now() - startTime) / 1000;
    hashrate = elapsed > 0 ? (batchSize / elapsed) : 0;

    cn._free(inputPtr);
    cn._free(outputPtr);

    // Report stats periodically
    postMessage({
        type: 'stats',
        hashrate: hashrate,
        totalHashes: totalHashes,
        acceptedShares: acceptedShares
    });

    // Continue mining (yield to event loop)
    if (mining) {
        setTimeout(mineLoop, 1);
    }
}

self.onmessage = function(e) {
    const data = e.data || {};

    if (data.type === 'init') {
        initWasm();
    } else if (data.type === 'job') {
        // New job from pool (via main thread WebSocket)
        currentJob = data.job;
        if (!mining) {
            mining = true;
            mineLoop();
        }
    } else if (data.type === 'stop') {
        mining = false;
        postMessage({ type: 'stopped' });
    } else if (data.type === 'stats') {
        postMessage({
            type: 'stats',
            hashrate: hashrate,
            totalHashes: totalHashes,
            acceptedShares: acceptedShares
        });
    }
};
