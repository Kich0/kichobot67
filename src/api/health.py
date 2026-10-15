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
    # 1. Try resource module (Linux / Unix / Render)
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

    def update_activity(self) -> None:
        """Records timestamp of the latest bot or webhook interaction."""
        self.last_activity = time.time()

    def get_uptime(self) -> int:
        """Returns uptime in seconds since monitor startup."""
        return int(time.time() - self.start_time)

    def get_time_since_last_activity(self) -> int:
        """Returns elapsed seconds since the latest bot interaction."""
        return int(time.time() - self.last_activity)

    async def check_database(self) -> bool:
        """Verifies active connectivity to MongoDB."""
        client = get_client()
        if client is None:
            return False
        try:
            # Quick admin ping command to verify database heartbeat
            if hasattr(client, "admin"):
                await client.admin.command("ping")
            return True
        except Exception as e:
            logger.warning(f"Database health check failed: {e}")
            return False

    async def is_healthy(self) -> dict[str, Any]:
        """Performs full health checks and returns detailed status payload."""
        uptime = self.get_uptime()
        time_since_activity = self.get_time_since_last_activity()

        # Bot is considered healthy if active within last 300 seconds (5 min)
        # OR if server freshly started within last 300 seconds
        bot_ok = (time_since_activity < 300) or (uptime < 300)

        db_ok = await self.check_database()

        rss_mb = get_process_memory_mb()
        overall_healthy = bot_ok and db_ok

        return {
            "status": "healthy" if overall_healthy else "unhealthy",
            "healthy": overall_healthy,
            "mode": settings.BOT_MODE,
            "uptime": uptime,
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# Singleton instance
health_monitor = HealthMonitor()
