"""In-memory rate limiter for FastAPI HTTP endpoints.

Protects public endpoints (/bot/log, /bot/get_user_schedule) from flooding and DoS.
"""

from __future__ import annotations

import time


class InMemoryRateLimiter:
    """Sliding window in-memory rate limiter keyed by client IP with bounded capacity."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float = 60.0,
        max_clients: int = 5000,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_clients = max_clients
        self.cleanup_interval_seconds = cleanup_interval_seconds
        self.clients: dict[str, list[float]] = {}
        self.last_cleanup = time.time()

    def _cleanup_stale_clients(self, now: float) -> None:
        """Removes client records with no activity within the sliding window, enforcing capacity limits."""
        cutoff = now - self.window_seconds
        stale_keys = [
            ip for ip, timestamps in self.clients.items()
            if not timestamps or timestamps[-1] < cutoff
        ]
        for ip in stale_keys:
            self.clients.pop(ip, None)
        self.last_cleanup = now

        # Strictly prevent unbounded growth: if capacity reached or exceeded, evict oldest entries
        if len(self.clients) >= self.max_clients:
            sorted_ips = sorted(
                self.clients.keys(),
                key=lambda k: self.clients[k][-1] if self.clients[k] else 0.0,
            )
            excess = len(self.clients) - self.max_clients + 1
            for ip in sorted_ips[:excess]:
                self.clients.pop(ip, None)

    def is_allowed(self, client_ip: str) -> bool:
        """Determines if a request from client_ip is permitted under rate limit."""
        now = time.time()

        # Clean stale clients periodically or if capacity reached
        if (now - self.last_cleanup > self.cleanup_interval_seconds) or (len(self.clients) >= self.max_clients):
            self._cleanup_stale_clients(now)

        cutoff = now - self.window_seconds
        timestamps = self.clients.get(client_ip, [])
        # Keep only timestamps in current sliding window
        valid_timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(valid_timestamps) >= self.max_requests:
            self.clients[client_ip] = valid_timestamps[-self.max_requests:]
            return False

        valid_timestamps.append(now)
        self.clients[client_ip] = valid_timestamps
        if len(self.clients) > self.max_clients:
            self._cleanup_stale_clients(now)
        return True

    def reset(self) -> None:
        """Resets all rate limit state (useful in testing)."""
        self.clients.clear()
        self.last_cleanup = time.time()


# Standard instances matching Node.js config
schedule_rate_limiter = InMemoryRateLimiter(max_requests=45, window_seconds=60.0)
log_rate_limiter = InMemoryRateLimiter(max_requests=10, window_seconds=60.0)
