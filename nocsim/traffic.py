"""Synthetic traffic patterns (the standard set from Dally & Towles, ch. 9).

Each pattern has two forms:

* a *sampler*, ``dest(src, rng) -> dst`` (or -1 for "this node sends
  nothing"), which the simulator calls once per generated packet;
* a *traffic matrix* T, where T[s, t] is the fraction of s's packets sent to
  t, used by the analytical throughput estimate in ``nocsim.metrics``.

Uniform random is the friendly average case. Permutations such as transpose
and bit-complement stress specific links, and that is where topologies
really differ.
"""

from __future__ import annotations

import random
from collections.abc import Callable

import numpy as np

Sampler = Callable[[int, random.Random], int]

PATTERNS = ("uniform", "transpose", "bit_complement", "tornado", "hotspot")


def _permutation(n: int, pattern: str) -> list[int]:
    """Destination of every node under a permutation pattern (-1 = idle)."""
    perm = []
    for s in range(n * n):
        r, c = divmod(s, n)
        if pattern == "transpose":  # (r, c) -> (c, r): stresses the diagonal
            t = c * n + r
        elif pattern == "bit_complement":  # (r, c) -> (n-1-r, n-1-c): everyone crosses the centre
            t = (n - 1 - r) * n + (n - 1 - c)
        elif pattern == "tornado":  # shift each row by ceil(n/2) - 1: adversarial for rings
            t = r * n + (c + (n + 1) // 2 - 1) % n
        else:
            raise ValueError(pattern)
        perm.append(t if t != s else -1)  # a node mapped to itself stays idle
    return perm


def sampler(pattern: str, n: int, hotspots: tuple[int, ...] | None = None, hot_fraction: float = 0.2) -> Sampler:
    """Destination sampler for an n x n network.

    ``hotspot``: each packet goes to one of ``hotspots`` (default: the centre
    router) with probability ``hot_fraction``, and to a uniformly random node
    otherwise. Models e.g. a shared memory controller.
    """
    num = n * n
    if pattern == "uniform":
        def uniform(src: int, rng: random.Random) -> int:
            t = rng.randrange(num - 1)
            return t + 1 if t >= src else t  # uniform over all nodes except src
        return uniform

    if pattern == "hotspot":
        hot = hotspots or ((n // 2) * n + n // 2,)
        def hotspot(src: int, rng: random.Random) -> int:
            if rng.random() < hot_fraction:
                t = hot[rng.randrange(len(hot))]
                if t != src:
                    return t
            t = rng.randrange(num - 1)
            return t + 1 if t >= src else t
        return hotspot

    perm = _permutation(n, pattern)
    return lambda src, rng: perm[src]


def matrix(pattern: str, n: int, hotspots: tuple[int, ...] | None = None, hot_fraction: float = 0.2) -> np.ndarray:
    """Traffic matrix T for ``pattern`` (rows sum to 1, or 0 for idle nodes)."""
    num = n * n
    uniform = np.full((num, num), 1.0 / (num - 1))
    np.fill_diagonal(uniform, 0.0)
    if pattern == "uniform":
        return uniform
    if pattern == "hotspot":
        hot = hotspots or ((n // 2) * n + n // 2,)
        t = (1 - hot_fraction) * uniform
        for s in range(num):
            for h in hot:
                if h != s:
                    t[s, h] += hot_fraction / len(hot)
                else:  # a hotspot "sending to itself" falls back to uniform
                    t[s] += hot_fraction / len(hot) * uniform[s]
        return t
    t = np.zeros((num, num))
    for s, d in enumerate(_permutation(n, pattern)):
        if d >= 0:
            t[s, d] = 1.0
    return t
