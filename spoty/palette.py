"""Dominant-colour sampling for the card tint (pure Python so it can be unit-tested)."""
from __future__ import annotations

from typing import Iterable, Tuple

RGB = Tuple[int, int, int]
DEFAULT = ((0x2A, 0x2A, 0x2E), (0x1B, 0x1B, 0x1F))


def _avg(s, w, i) -> RGB:
    return (int(s[0][i] / w[i]), int(s[1][i] / w[i]), int(s[2][i] / w[i]))


def _dist(a: RGB, b: RGB) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def dominant_colors(pixels: Iterable[RGB]) -> Tuple[RGB, RGB]:
    """Two distinct dominant colours, favouring saturated mid-brightness pixels."""
    w = [0.0] * 4096
    sums = ([0.0] * 4096, [0.0] * 4096, [0.0] * 4096)
    for r, g, b in pixels:
        mx, mn = max(r, g, b), min(r, g, b)
        sat = 0.0 if mx == 0 else (mx - mn) / mx
        lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
        wt = 0.25 + sat
        if lum < 0.08 or lum > 0.95:
            wt *= 0.3
        i = ((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4)
        w[i] += wt
        sums[0][i] += r * wt
        sums[1][i] += g * wt
        sums[2][i] += b * wt
    best = max(range(4096), key=lambda i: w[i])
    if w[best] <= 0:
        return DEFAULT
    c1 = _avg(sums, w, best)
    second = None
    for i in range(4096):
        if w[i] < w[best] * 0.12 or _dist(_avg(sums, w, i), c1) < 70:
            continue
        if second is None or w[i] > w[second]:
            second = i
    c2 = _avg(sums, w, second) if second is not None else tuple(int(c * 0.65) for c in c1)
    return c1, c2


def tone(rgb: RGB, max_lum: float = 0.30) -> RGB:
    """Darken a colour so white text always stays readable on it."""
    r, g, b = rgb
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    f = max_lum / lum if lum > max_lum else 1.0
    return (int(r * f), int(g * f), int(b * f))
