"""K-gram hashing and winnowing (Schleimer/Wilkerson/Aiken 2003).

Winnowing guarantees:
    - Any match of length >= w + k - 1 tokens is detected
    - No match shorter than k tokens detected (noise threshold)
"""
from __future__ import annotations

import hashlib


def kgram_hashes(tokens: list[str], k: int) -> list[int]:
    """Hash each k-gram of the token stream using blake2b for stability across runs."""
    if len(tokens) < k:
        return []
    out: list[int] = []
    for i in range(len(tokens) - k + 1):
        gram = "\x1f".join(tokens[i:i + k])
        h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        out.append(int.from_bytes(h, "big"))
    return out


def winnow(hashes: list[int], w: int) -> set[int]:
    """Select fingerprint subset via sliding window of size w; pick rightmost min per window.

    Per Schleimer et al., rightmost-min selection ensures the same hash gets
    selected from overlapping windows when possible, reducing fingerprint size.
    """
    if not hashes:
        return set()
    if len(hashes) <= w:
        return {min(hashes)}

    fingerprints: set[int] = set()
    last_selected_idx = -1

    for i in range(len(hashes) - w + 1):
        window = hashes[i:i + w]
        # Rightmost minimum: scan right-to-left, pick first hit.
        min_val = window[0]
        min_idx = 0
        for j in range(1, w):
            if window[j] <= min_val:
                min_val = window[j]
                min_idx = j
        absolute_idx = i + min_idx
        if absolute_idx != last_selected_idx:
            fingerprints.add(min_val)
            last_selected_idx = absolute_idx

    return fingerprints


def fingerprint(tokens: list[str], k: int = 5, w: int = 4) -> set[int]:
    return winnow(kgram_hashes(tokens, k), w)
