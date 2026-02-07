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
        cn = await CryptoNight({
            locateFile: (path) => '/static/wasm/' + path
        });
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

function parseTarget(targetHex) {
    // Pool sends target as little-endian hex (e.g. "b4b0bf00" = difficulty ~84)
    // We need to compare the HIGH 32 bits of the hash (bytes 28-31) against the
    // target interpreted as little-endian uint32.
    // Convert LE hex to actual uint32 value:
    const bytes = hexToBytes(targetHex.padEnd(8, '0').slice(0, 8));
    // Little-endian: first byte is lowest
    return (bytes[0]) | (bytes[1] << 8) | (bytes[2] << 16) | ((bytes[3] << 24) >>> 0);
}

function mineLoop() {
    if (!mining || !currentJob || !cn) return;

    const blob = hexToBytes(currentJob.blob);
    const blobLen = blob.length;
    const target = parseTarget(currentJob.target);

    // Allocate WASM memory
    const inputPtr = cn._malloc(blobLen);
    const outputPtr = cn._malloc(32);
    cn.HEAPU8.set(blob, inputPtr);

    const batchSize = 64;
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

        // Check hash against target
        // Stratum: compare high 32 bits of hash (bytes 28-31 LE) against target
        const hashBytes = new Uint8Array(cn.HEAPU8.buffer, outputPtr, 32);
        const hashHigh32 = (hashBytes[28]) | (hashBytes[29] << 8) | (hashBytes[30] << 16) | ((hashBytes[31] << 24) >>> 0);

        if (hashHigh32 <= target && target > 0) {
            // Found valid share!
            // Nonce for submission: as it appears in the blob (already LE in memory)
            const nonceHex = [
                (nonce & 0xFF).toString(16).padStart(2, '0'),
                ((nonce >> 8) & 0xFF).toString(16).padStart(2, '0'),
                ((nonce >> 16) & 0xFF).toString(16).padStart(2, '0'),
                ((nonce >> 24) & 0xFF).toString(16).padStart(2, '0')
            ].join('');

            const resultHex = bytesToHex(hashBytes);

            postMessage({
                type: 'share',
                nonce: nonceHex,
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

    // Continue mining with small delay to avoid UI freeze
    if (mining) {
        setTimeout(mineLoop, 10);
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
