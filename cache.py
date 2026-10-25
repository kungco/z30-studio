"""Thread-safe byte-bounded cache; derived data is scoped to an immutable input."""
from collections import OrderedDict
import sys
import threading


def weight(value):
    if hasattr(value, 'nbytes'):
        base = getattr(value, 'base', None)
        return max(int(value.nbytes), weight(base) if base is not None else 0)
    if isinstance(value, dict):
        return sys.getsizeof(value) + sum(weight(k) + weight(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sys.getsizeof(value) + sum(map(weight, value))
    return sys.getsizeof(value)


class ByteLRU:
    def __init__(self, max_bytes):
        self.limit = max_bytes
        self.used = 0
        self.items = OrderedDict()
        self.lock = threading.RLock()
        self.hits = self.misses = 0

    def get(self, key):
        with self.lock:
            if key not in self.items:
                self.misses += 1
                return None
            self.hits += 1
            value, size = self.items.pop(key)
            self.items[key] = (value, size)
            return value

    def put(self, key, value):
        size = weight(key) + weight(value)
        with self.lock:
            if key in self.items:
                self.used -= self.items.pop(key)[1]
            if size > self.limit:
                return
            while self.items and self.used + size > self.limit:
                self.used -= self.items.popitem(last=False)[1][1]
            self.items[key] = (value, size)
            self.used += size

    def remove_source(self, source):
        with self.lock:
            for key in list(self.items):
                if key[0] == source:
                    self.used -= self.items.pop(key)[1]

    def stats(self):
        with self.lock:
            return dict(bytes=self.used, limit_bytes=self.limit, entries=len(self.items), hits=self.hits, misses=self.misses)
