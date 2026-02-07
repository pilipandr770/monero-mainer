/**
 * CryptoNight v0 (cn/0) implementation for WASM.
 *
 * Includes:
 *  - Keccak-f[1600] (standard permutation)
 *  - Software AES with correct SubBytes + ShiftRows + MixColumns + AddRoundKey
 *  - AES-256 key expansion
 *  - CryptoNight main algorithm (2 MB scratchpad, 524288 iterations)
 *  - Final hash selection: Blake-256 / Groestl-256 / JH-256 / Skein-256
 *    (uses Monero's proven implementations linked at compile time)
 *
 * Compile with:
 *   emcc cryptonight_impl.c blake256.c groestl.c jh.c skein.c ...
 */

#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#ifdef __EMSCRIPTEN__
#include <emscripten.h>
#else
#define EMSCRIPTEN_KEEPALIVE
#endif

/* ========================= Keccak-f[1600] ========================= */

static const uint64_t keccak_rc[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL
};

#define ROTL64(x, y) (((x) << (y)) | ((x) >> (64 - (y))))

static void keccakf(uint64_t st[25]) {
    for (int round = 0; round < 24; round++) {
        /* Theta */
        uint64_t bc[5];
        for (int i = 0; i < 5; i++)
            bc[i] = st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20];
        for (int i = 0; i < 5; i++) {
            uint64_t t = bc[(i + 4) % 5] ^ ROTL64(bc[(i + 1) % 5], 1);
            for (int j = 0; j < 25; j += 5)
                st[j + i] ^= t;
        }
        /* Rho + Pi */
        uint64_t t = st[1];
        static const int piln[24] = {
            10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
            15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1
        };
        static const int rotc[24] = {
            1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
            27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44
        };
        for (int i = 0; i < 24; i++) {
            int j = piln[i];
            uint64_t temp = st[j];
            st[j] = ROTL64(t, rotc[i]);
            t = temp;
        }
        /* Chi */
        for (int j = 0; j < 25; j += 5) {
            uint64_t tmp[5];
            for (int i = 0; i < 5; i++)
                tmp[i] = st[j + i];
            for (int i = 0; i < 5; i++)
                st[j + i] = tmp[i] ^ ((~tmp[(i + 1) % 5]) & tmp[(i + 2) % 5]);
        }
        /* Iota */
        st[0] ^= keccak_rc[round];
    }
}

/**
 * Keccak-1600 hash.  rate = 1088 bits = 136 bytes.
 * Outputs full 200-byte state (needed for CryptoNight).
 * Uses original Keccak padding (0x01...0x80), NOT SHA-3 (0x06).
 */
static void keccak1600(const uint8_t *in, size_t inlen, uint8_t *md) {
    uint64_t st[25];
    const int rsiz = 136;           /* rate in bytes */
    memset(st, 0, sizeof(st));

    /* Absorb full blocks */
    for (; inlen >= (size_t)rsiz; inlen -= rsiz, in += rsiz) {
        for (int i = 0; i < rsiz / 8; i++) {
            uint64_t v;
            memcpy(&v, in + i * 8, 8);
            st[i] ^= v;
        }
        keccakf(st);
    }

    /* Pad last block */
    uint8_t temp[136];
    memset(temp, 0, rsiz);
    memcpy(temp, in, inlen);
    temp[inlen] = 0x01;             /* original Keccak padding */
    temp[rsiz - 1] |= 0x80;
    for (int i = 0; i < rsiz / 8; i++) {
        uint64_t v;
        memcpy(&v, temp + i * 8, 8);
        st[i] ^= v;
    }
    keccakf(st);

    /* Output full 200-byte state */
    memcpy(md, st, 200);
}

/* ========================= Software AES ========================= */

static const uint8_t aes_sbox[256] = {
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16
};

/** GF(2^8) multiply by 2 with reduction by x^8+x^4+x^3+x+1 */
static inline uint8_t xtime(uint8_t x) {
    return (uint8_t)((x << 1) ^ (((x >> 7) & 1) * 0x1b));
}

/**
 * Single AES round: SubBytes → ShiftRows → MixColumns → AddRoundKey.
 * This matches Monero's aesb_single_round().
 */
static void aes_single_round(uint8_t *out, const uint8_t *in, const uint8_t *key) {
    uint8_t t[16], s[16];

    /* SubBytes */
    for (int i = 0; i < 16; i++)
        t[i] = aes_sbox[in[i]];

    /* ShiftRows  (row 0: no shift, row 1: <<1, row 2: <<2, row 3: <<3)
     * AES state is column-major: index = row + 4*col                       */
    s[ 0] = t[ 0]; s[ 1] = t[ 5]; s[ 2] = t[10]; s[ 3] = t[15];
    s[ 4] = t[ 4]; s[ 5] = t[ 9]; s[ 6] = t[14]; s[ 7] = t[ 3];
    s[ 8] = t[ 8]; s[ 9] = t[13]; s[10] = t[ 2]; s[11] = t[ 7];
    s[12] = t[12]; s[13] = t[ 1]; s[14] = t[ 6]; s[15] = t[11];

    /* MixColumns: multiply each column by the MDS matrix
     *  [2 3 1 1]
     *  [1 2 3 1]
     *  [1 1 2 3]
     *  [3 1 1 2]                                                            */
    for (int c = 0; c < 4; c++) {
        uint8_t a0 = s[c*4+0], a1 = s[c*4+1], a2 = s[c*4+2], a3 = s[c*4+3];
        uint8_t x0 = xtime(a0), x1 = xtime(a1), x2 = xtime(a2), x3 = xtime(a3);
        s[c*4+0] = x0 ^ x1 ^ a1 ^ a2 ^ a3;   /* 2*a0 + 3*a1 + a2   + a3   */
        s[c*4+1] = a0 ^ x1 ^ x2 ^ a2 ^ a3;   /* a0   + 2*a1 + 3*a2 + a3   */
        s[c*4+2] = a0 ^ a1 ^ x2 ^ x3 ^ a3;   /* a0   + a1   + 2*a2 + 3*a3 */
        s[c*4+3] = x0 ^ a0 ^ a1 ^ a2 ^ x3;   /* 3*a0 + a1   + a2   + 2*a3 */
    }

    /* AddRoundKey */
    for (int i = 0; i < 16; i++)
        out[i] = s[i] ^ key[i];
}

/**
 * AES-256 key expansion: 32-byte key → 240 bytes (15 round keys).
 * CryptoNight's aesb_pseudo_round uses the first 10 round keys (160 bytes).
 */
static void aes256_expand_key(const uint8_t *key, uint8_t expanded[240]) {
    static const uint8_t rcon[7] = {0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40};

    memcpy(expanded, key, 32);

    int n = 32;
    int rcon_idx = 0;

    while (n < 240) {
        uint8_t temp[4];
        memcpy(temp, expanded + n - 4, 4);

        if (n % 32 == 0) {
            /* RotWord + SubWord + Rcon */
            uint8_t t0 = temp[0];
            temp[0] = aes_sbox[temp[1]] ^ rcon[rcon_idx++];
            temp[1] = aes_sbox[temp[2]];
            temp[2] = aes_sbox[temp[3]];
            temp[3] = aes_sbox[t0];
        } else if (n % 32 == 16) {
            /* SubWord only (AES-256 specific) */
            for (int i = 0; i < 4; i++)
                temp[i] = aes_sbox[temp[i]];
        }

        for (int i = 0; i < 4; i++) {
            expanded[n] = expanded[n - 32] ^ temp[i];
            n++;
        }
    }
}

/**
 * 10-round AES "pseudo round" — what Monero calls aesb_pseudo_round().
 * Applies SubBytes+ShiftRows+MixColumns+AddRoundKey 10 times,
 * using consecutive 16-byte round keys from the expanded key.
 */
static void aes_pseudo_round(uint8_t *data, const uint8_t *expanded_key) {
    for (int r = 0; r < 10; r++)
        aes_single_round(data, data, expanded_key + r * 16);
}

/* =================== Final hash function externs =================== */
/* These are provided by Monero's blake256.c, groestl.c, jh.c, skein.c */

extern void blake256_hash(uint8_t *out, const uint8_t *in, uint64_t inlen);
extern void groestl(const uint8_t *data, unsigned long long databitlen, uint8_t *hashval);
extern int  jh_hash(int hashbitlen, const uint8_t *data, unsigned long long databitlen, uint8_t *hashval);
extern int  skein_hash(int hashbitlen, const uint8_t *data, unsigned long long databitlen, uint8_t *hashval);

/* ========================= CryptoNight v0 ========================= */

#define CN_MEMORY       2097152     /* 2 MB scratchpad */
#define CN_ITER         524288      /* number of iterations (loop runs ITER/2) */
#define AES_BLOCK_SIZE  16
#define AES_KEY_SIZE    32
#define INIT_SIZE_BYTE  128         /* 8 AES blocks */

static inline void xor_blocks(uint8_t *a, const uint8_t *b) {
    for (int i = 0; i < 16; i++) a[i] ^= b[i];
}

/**
 * 64×64 → 128-bit multiply.
 * Produces high and low 64-bit halves of (a * b).
 */
static inline void mul_128(uint64_t a, uint64_t b, uint64_t *hi, uint64_t *lo) {
    uint64_t a_lo = (uint32_t)a;
    uint64_t a_hi = a >> 32;
    uint64_t b_lo = (uint32_t)b;
    uint64_t b_hi = b >> 32;

    uint64_t p0 = a_lo * b_lo;
    uint64_t p1 = a_lo * b_hi;
    uint64_t p2 = a_hi * b_lo;
    uint64_t p3 = a_hi * b_hi;

    uint64_t mid = p1 + (p0 >> 32);
    mid += p2;
    if (mid < p2)
        p3 += 0x100000000ULL;       /* carry */

    *lo = (mid << 32) | (uint32_t)p0;
    *hi = p3 + (mid >> 32);
}

/**
 * CryptoNight v0 (cn/0) hash function.
 *
 * Algorithm (portable path from Monero's slow-hash.c with variant=0):
 *  1. Keccak-1600(input) → 200-byte state
 *  2. AES-256 key expansion using state[0..31]
 *  3. Initialize 2 MB scratchpad (10-round AES per block)
 *  4. Main loop: 524288 operations (262144 iterations × 2 sub-steps)
 *     4a. AES single round + XOR + write
 *     4b. 64-bit multiply + accumulate + XOR + write
 *  5. Finalize: XOR scratchpad back + AES (key from state[32..63])
 *  6. Keccak-f permutation on state
 *  7. Select final hash: Blake-256 / Groestl-256 / JH-256 / Skein-256
 */
EMSCRIPTEN_KEEPALIVE
void cn_hash(const uint8_t *input, uint32_t input_len, uint8_t *output) {
    uint8_t  state[200];
    uint8_t  text[INIT_SIZE_BYTE];
    uint8_t  expanded_key[240];
    uint8_t *hp_state;

    hp_state = (uint8_t *)malloc(CN_MEMORY);
    if (!hp_state) return;

    /* --- Step 1: Keccak → 200-byte state --- */
    keccak1600(input, input_len, state);

    /* --- Step 2: AES-256 key expansion (first 32 bytes of state) --- */
    aes256_expand_key(state, expanded_key);

    /* --- Step 3: Initialize scratchpad --- */
    memcpy(text, state + 64, INIT_SIZE_BYTE);
    for (uint32_t i = 0; i < CN_MEMORY; i += INIT_SIZE_BYTE) {
        for (int j = 0; j < INIT_SIZE_BYTE; j += AES_BLOCK_SIZE)
            aes_pseudo_round(text + j, expanded_key);
        memcpy(hp_state + i, text, INIT_SIZE_BYTE);
    }

    /* --- Step 4: Main loop --- */
    /* a = state[0..15] XOR state[32..47]
     * b = state[16..31] XOR state[48..63]  */
    uint64_t a[2], b[2];
    {
        uint64_t s0, s1, s2, s3;
        memcpy(&s0, state +  0, 8);  memcpy(&s1, state +  8, 8);
        memcpy(&s2, state + 32, 8);  memcpy(&s3, state + 40, 8);
        a[0] = s0 ^ s2;  a[1] = s1 ^ s3;

        memcpy(&s0, state + 16, 8);  memcpy(&s1, state + 24, 8);
        memcpy(&s2, state + 48, 8);  memcpy(&s3, state + 56, 8);
        b[0] = s0 ^ s2;  b[1] = s1 ^ s3;
    }

    for (uint32_t i = 0; i < CN_ITER / 2; i++) {
        /* ------ Sub-step A: AES round ------ */
        uint32_t j1 = ((uint32_t)a[0]) & 0x1FFFF0;
        uint8_t  c1[16];
        memcpy(c1, hp_state + j1, 16);
        aes_single_round(c1, c1, (const uint8_t *)a);

        /* Write (c1 XOR b) to scratchpad, then b ← c1 */
        {
            uint64_t c1_64[2], *sp = (uint64_t *)(hp_state + j1);
            memcpy(c1_64, c1, 16);
            sp[0] = c1_64[0] ^ b[0];
            sp[1] = c1_64[1] ^ b[1];
        }

        /* ------ Sub-step B: Multiply ------ */
        uint64_t c1_64[2];
        memcpy(c1_64, c1, 16);
        uint32_t j2 = ((uint32_t)c1_64[0]) & 0x1FFFF0;
        uint64_t *p2 = (uint64_t *)(hp_state + j2);
        uint64_t c2_0 = p2[0], c2_1 = p2[1];

        uint64_t hi, lo;
        mul_128(c1_64[0], c2_0, &hi, &lo);

        a[0] += hi;
        a[1] += lo;

        /* Write updated a to scratchpad */
        p2[0] = a[0];
        p2[1] = a[1];

        /* XOR a with original scratchpad value */
        a[0] ^= c2_0;
        a[1] ^= c2_1;

        /* b ← c1 */
        b[0] = c1_64[0];
        b[1] = c1_64[1];
    }

    /* --- Step 5: Finalize scratchpad → state --- */
    aes256_expand_key(state + 32, expanded_key);
    memcpy(text, state + 64, INIT_SIZE_BYTE);
    for (uint32_t i = 0; i < CN_MEMORY; i += INIT_SIZE_BYTE) {
        for (int j = 0; j < INIT_SIZE_BYTE; j += AES_BLOCK_SIZE) {
            xor_blocks(text + j, hp_state + i + j);
            aes_pseudo_round(text + j, expanded_key);
        }
    }
    memcpy(state + 64, text, INIT_SIZE_BYTE);

    /* --- Step 6: Final Keccak-f permutation --- */
    keccakf((uint64_t *)state);

    /* --- Step 7: Select final hash --- */
    switch (state[0] & 3) {
        case 0:  blake256_hash(output, state, 200);                 break;
        case 1:  groestl(state, (unsigned long long)200 * 8, output); break;
        case 2:  jh_hash(256, state, (unsigned long long)200 * 8, output); break;
        default: skein_hash(256, state, (unsigned long long)200 * 8, output); break;
    }

    free(hp_state);
}

/* ======================== WASM API exports ======================== */

EMSCRIPTEN_KEEPALIVE
uint32_t get_memory_size(void) {
    return CN_MEMORY;
}

EMSCRIPTEN_KEEPALIVE
int try_hash(const uint8_t *blob, uint32_t blob_len, uint32_t nonce,
             uint64_t target, uint8_t *out_hash)
{
    uint8_t input[256];
    if (blob_len > 256) return 0;
    memcpy(input, blob, blob_len);
    /* Set nonce at offset 39 (little-endian) */
    if (blob_len >= 43) {
        input[39] = (uint8_t)(nonce & 0xFF);
        input[40] = (uint8_t)((nonce >> 8)  & 0xFF);
        input[41] = (uint8_t)((nonce >> 16) & 0xFF);
        input[42] = (uint8_t)((nonce >> 24) & 0xFF);
    }
    cn_hash(input, blob_len, out_hash);

    uint64_t hash_val;
    memcpy(&hash_val, out_hash + 24, 8);
    return (hash_val < target) ? 1 : 0;
}
