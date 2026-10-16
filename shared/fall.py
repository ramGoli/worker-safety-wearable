# Fall detector shared by device/main.py (MicroPython) and tests (CPython).
# Feed the acceleration magnitude in g plus a monotonic millisecond clock. A fall is
# free-fall (< free_fall_g for >= min_free_fall_ms), then an impact (> impact_g within
# impact_window_ms), then settle_ms of tracking the peak.
# On the device, now_ms is time.ticks_ms(), which wraps after about 12 days; fine for a demo.
IDLE, FREE_FALL, AWAIT_IMPACT, SETTLE = 0, 1, 2, 3


class FallDetector:
    def __init__(self, free_fall_g=0.5, min_free_fall_ms=80, impact_g=2.5,
                 impact_window_ms=1000, settle_ms=300, cooldown_ms=3000):
        self.free_fall_g = free_fall_g
        self.min_free_fall_ms = min_free_fall_ms
        self.impact_g = impact_g
        self.impact_window_ms = impact_window_ms
        self.settle_ms = settle_ms
        self.cooldown_ms = cooldown_ms
        self.state = IDLE
        self.peak_g = 0.0
        self._t = 0
        self._last_fall_ms = None

    def step(self, g, now_ms):
        """Returns True exactly once per detected fall."""
        if self._last_fall_ms is not None and now_ms - self._last_fall_ms < self.cooldown_ms:
            return False
        if self.state == IDLE:
            if g < self.free_fall_g:
                self.state, self._t = FREE_FALL, now_ms
            return False
        if self.state == FREE_FALL:
            if g < self.free_fall_g:
                return False
            if now_ms - self._t < self.min_free_fall_ms:
                self.state = IDLE
                return False
            self.state, self._t, self.peak_g = AWAIT_IMPACT, now_ms, g
            # fall through: the sample that ends free-fall may itself be the impact
        if self.state == AWAIT_IMPACT:
            self.peak_g = max(self.peak_g, g)
            if g > self.impact_g:
                self.state, self._t = SETTLE, now_ms
            elif now_ms - self._t > self.impact_window_ms:
                self.state = IDLE
            return False
        self.peak_g = max(self.peak_g, g)
        if now_ms - self._t >= self.settle_ms:
            self.state = IDLE
            self._last_fall_ms = now_ms
            return True
        return False
