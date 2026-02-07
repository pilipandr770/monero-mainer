/*
 * CryptoNight hash function - Self-contained implementation for WebAssembly
 * Based on the Monero reference implementation (portable C fallback).
 * Implements CryptoNight variant 0 (cn/0).
 *
 * Copyright (c) 2012-2013 The CryptoNote developers
 * Copyright (c) 2014-2024 The Monero Project
 * Portions Copyright (c) 1998-2013, Brian Gladman (AES)
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted.
 */

#ifndef CRYPTONIGHT_H
#define CRYPTONIGHT_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Main CryptoNight hash function:  cn/0 (variant 0)
 *   input: pointer to input data
 *   len:   length of input data in bytes
 *   output: pointer to 32-byte output buffer
 */
void cn_hash(const uint8_t *input, size_t len, uint8_t *output);

/* Lower-level interface matching Monero's cn_slow_hash:
 *   variant: must be 0 for cn/0
 */
void cn_slow_hash(const void *data, size_t length, char *hash, int variant, int prehashed, uint64_t height);

#ifdef __cplusplus
}
#endif

#endif /* CRYPTONIGHT_H */
