# Sound level of one short mic recording, shared by device/main.py (MicroPython) and tests (CPython).
# MicroPython-compatible: no typing, no slice deletion.
import math

FLOOR_RMS = 1  # silence reads as -90 dBFS instead of -infinity


def ac_dbfs(samples, step=1):
    """AC RMS level in dBFS of int16 samples, ignoring the mic's DC offset. step > 1 uses every step-th sample,
    which keeps the Python loop short on the stick (the level barely changes)."""
    n = s = s2 = 0
    for i in range(0, len(samples), step):
        v = samples[i]
        s += v
        s2 += v * v
        n += 1
    mean = s / n
    rms = math.sqrt(max(s2 / n - mean * mean, FLOOR_RMS * FLOOR_RMS))
    return 20 * math.log10(rms / 32768)
