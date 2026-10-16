"""Application-wide constants for Kichobot.

Cache rules, rate limits, cooldowns, and system timeouts adhere to
the production specifications in AGENTS.md and python_migration_plan.md.
"""

from datetime import timedelta
import zoneinfo

# Timezone
TIMEZONE_NAME = "Asia/Almaty"
TIMEZONE = zoneinfo.ZoneInfo(TIMEZONE_NAME)

# Cache Lifetimes (RAM TTLCache)
CACHE_STUDENT_TEXT_TTL = timedelta(minutes=30)
CACHE_TEACHER_TEXT_TTL = timedelta(minutes=30)
CACHE_TEACHER_IMAGE_TTL = timedelta(minutes=20)
CACHE_API_STUDENTS_TTL = timedelta(minutes=10)
CACHE_API_TEACHERS_TTL = timedelta(minutes=10)

# Refresh Button (🔄) Cooldowns
COOLDOWN_STUDENT_REFRESH = timedelta(minutes=10)
COOLDOWN_TEACHER_TEXT_REFRESH = timedelta(minutes=5)
COOLDOWN_TEACHER_IMAGE_REFRESH = timedelta(minutes=20)

# LRU Cache Sizes (Capacity)
CACHE_CAPACITY_STUDENT_GROUPS = 1000
CACHE_CAPACITY_TEACHERS = 300
CACHE_CAPACITY_TEACHER_IMAGES = 160

# Buketov University Schedule API
SCHEDULE_API_TIMEOUT = 4.5  # seconds
SCHEDULE_API_RATE_LIMIT = 110  # max requests per minute (university limit is 120/min/IP)
CIRCUIT_BREAKER_FAIL_THRESHOLD = 2
CIRCUIT_BREAKER_RESET_TIMEOUT = 120.0  # seconds (2 minutes)

# Message Gate & Anti-Spam
MESSAGE_GATE_THROTTLE_INTERVAL = 0.5  # seconds (500ms between interactions)
ANTI_SPAM_WINDOW = 3.0  # seconds
ANTI_SPAM_MAX_REQUESTS = 5
ANTI_SPAM_FIRST_BAN_DURATION = 60.0  # seconds (1 minute)
ANTI_SPAM_REPEAT_WINDOW = 300.0  # seconds (5 minutes)
ANTI_SPAM_REPEAT_BAN_DURATION = 300.0  # seconds (5 minutes)
ANTI_SPAM_BAN_DURATION = ANTI_SPAM_FIRST_BAN_DURATION  # backwards compatibility alias

# Database TTLs
USER_ACTION_TTL_DAYS = 30
SCHEDULE_VERSION_TTL_DAYS = 60
SCHEDULE_HISTORY_MAX_MB = 50

# Contacts & References
DEVELOPER_USERNAME = "@Kicho_0"
OFFICIAL_SCHEDULE_URL = "https://schedule.karnu-buketov.edu.kz"
