"""Health monitoring service for Kichobot.

Tracks bot activity, database connectivity, uptime, and memory consumption.
Adheres to the health specifications in botHealthMonitor.js.
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger
from src.database.connection import get_client

logger = get_logger("health_monitor")


def get_process_memory_mb() -> float:
    """Returns the process RSS memory in megabytes."""
    # 1. Try reading real-time VmRSS from /proc/self/status on Linux (Render)
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return round(float(parts[1]) / 1024.0, 2)
    except Exception:
        pass

    # 2. Try resource module (Linux / Unix / Render fallback)
    try:
        import resource

        rusage = resource.getrusage(resource.RUSAGE_SELF)
        # ru_maxrss is in kilobytes on Linux
        return round(rusage.ru_maxrss / 1024.0, 2)
    except (ImportError, AttributeError):
        pass

    # 2. Try ctypes Windows API
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        psapi = ctypes.windll.psapi
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        pmc = PROCESS_MEMORY_COUNTERS_EX()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        if psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.WorkingSetSize / (1024.0 * 1024.0), 2)
    except Exception:
        pass

    return 0.0


class HealthMonitor:
    """Monitors application uptime, bot activity, database state, and memory."""

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.last_activity: float = time.time()
        self._last_db_check: float = 0.0
        self._cached_db_status: bool = True
        self._db_cache_ttl: float = 20.0

    def update_activity(self) -> None:
        """Records timestamp of the latest bot or webhook interaction."""
        self.last_activity = time.time()

    def get_uptime(self) -> int:
        """Returns uptime in seconds since monitor startup."""
        return int(time.time() - self.start_time)

    def get_time_since_last_activity(self) -> int:
        """Returns elapsed seconds since the latest bot interaction."""
        return int(time.time() - self.last_activity)

    async def check_database(self, force: bool = False) -> bool:
        """Verifies active connectivity to MongoDB (cached for 20s to reduce DB ping load)."""
        now = time.time()
        if not force and (now - self._last_db_check) < self._db_cache_ttl:
            return self._cached_db_status

        client = get_client()
        if client is None:
            self._cached_db_status = False
            self._last_db_check = now
            return False
        try:
            # Quick admin ping command to verify database heartbeat
            if hasattr(client, "admin"):
                await client.admin.command("ping")
            self._cached_db_status = True
            self._last_db_check = now
            return True
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            self._cached_db_status = False
            self._last_db_check = now
            return False

    async def is_healthy(self) -> dict[str, Any]:
        """Performs full health checks and returns detailed status payload."""
        uptime = self.get_uptime()
        time_since_activity = self.get_time_since_last_activity()

        # In webhook mode on Render, idle periods without incoming messages must NOT trigger 503
        if settings.BOT_MODE == "webhook":
            bot_ok = True
        else:
            bot_ok = (time_since_activity < 300) or (uptime < 300)

        db_ok = await self.check_database()

        rss_mb = get_process_memory_mb()
        overall_healthy = bot_ok and db_ok

        try:
            from src.services.buketov_api import buketov_api_service

            buketov_api_status = getattr(buketov_api_service, "circuit_state", "CLOSED")
        except Exception:
            buketov_api_status = "UNKNOWN"

        return {
            "status": "healthy" if overall_healthy else "unhealthy",
            "healthy": overall_healthy,
            "mode": settings.BOT_MODE,
            "uptime": uptime,
            "uptime_seconds": uptime,
            "timeSinceLastActivity": time_since_activity,
            "checks": {
                "bot": bot_ok,
                "database": db_ok,
                "uptime": uptime,
                "timeSinceLastActivity": time_since_activity,
            },
            "memory": {
                "rss_mb": rss_mb,
            },
            "memory_mb": rss_mb,
            "buketov_api": buketov_api_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def get_status(self) -> dict[str, Any]:
        """Alias for is_healthy() used by admin /info diagnostics."""
        return await self.is_healthy()


# Singleton instance
health_monitor = HealthMonitor()
