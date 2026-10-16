# Bounded FIFO of events waiting for Zerobus. When full, the oldest heartbeat is evicted
# first, so falls, SOS and acks survive a long Wi-Fi outage. MicroPython-compatible.


class Outbox:
    def __init__(self, maxlen=200):
        self.maxlen = maxlen
        self._items = []

    def __len__(self):
        return len(self._items)

    def push(self, event):
        self._items.append(event)
        if len(self._items) <= self.maxlen:
            return
        for i, e in enumerate(self._items):
            if e.get("event_type") == "heartbeat":
                self._items.pop(i)
                return
        self._items.pop(0)

    def peek(self, n):
        return self._items[:n]

    def drop(self, n):
        self._items = self._items[n:]
