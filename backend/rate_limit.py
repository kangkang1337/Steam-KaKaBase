"""Small process-local IP rate limiter for account and favorite mutations.

Nginx applies the first layer at the public edge.  This second layer keeps the
same protection if the application is accidentally exposed through another
proxy.  It intentionally stores no IP addresses on disk.
"""

from collections import defaultdict, deque
from threading import Lock
from time import monotonic


class IpRateLimiter:
    def __init__(self):
        self._events = defaultdict(deque)
        self._lock = Lock()

    def check(self, bucket, ip_address, *, limit, window_seconds):
        """Return retry seconds when the bucket is full, otherwise ``None``."""
        now = monotonic()
        key = (str(bucket), str(ip_address or "unknown"))
        with self._lock:
            events = self._events[key]
            cutoff = now - max(1, int(window_seconds))
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max(1, int(limit)):
                return max(1, int(events[0] + window_seconds - now) + 1)
            events.append(now)
        return None
