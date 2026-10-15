"""Service layer for Kichobot.

Includes:
- buketov_api: Buketov KarU Schedule REST API client with Circuit Breaker and rate limiting.
- schedule_adapter: Conversion of raw API records into bot schedule formats.
- teacher_directory: Teacher directory with Kazakh/Cyrillic normalization and profile enrichment.
- smart_search: Intelligent text search with RapidFuzz for groups and teachers.
"""

from src.services.buketov_api import (
    BuketovApiError,
    BuketovApiService,
    CacheEntry,
    CircuitBreakerError,
    buketov_api_service,
)
from src.services.schedule_adapter import (
    ScheduleApiAdapter,
    schedule_api_adapter,
)
from src.services.smart_search import (
    FuzzySuggestion,
    SearchResultType,
    SmartSearchResult,
    SmartSearchService,
    calculate_similarity,
    smart_search_service,
)
from src.services.teacher_directory import (
    TeacherDirectoryService,
    clean_titles,
    format_initials,
    format_title_case,
    is_kazakh_text,
    is_same_teacher,
    normalize_cyrillic,
    teacher_directory_service,
)

from src.services.table_image import (
    COLORS,
    DAYS_KZ,
    DAYS_RU,
    STANDARD_TIMES,
    TeacherTableImageService,
    escape_xml,
    format_lesson_type_badge,
    layout_cell_content,
    normalize_time,
    parse_group_slot,
    split_subject_text,
    table_image_service,
    wrap_groups_to_lines,
)

__all__ = [
    "BuketovApiError",
    "BuketovApiService",
    "COLORS",
    "CacheEntry",
    "CircuitBreakerError",
    "DAYS_KZ",
    "DAYS_RU",
    "FuzzySuggestion",
    "STANDARD_TIMES",
    "ScheduleApiAdapter",
    "SearchResultType",
    "SmartSearchResult",
    "SmartSearchService",
    "TeacherDirectoryService",
    "TeacherTableImageService",
    "buketov_api_service",
    "calculate_similarity",
    "clean_titles",
    "escape_xml",
    "format_initials",
    "format_lesson_type_badge",
    "format_title_case",
    "is_kazakh_text",
    "is_same_teacher",
    "layout_cell_content",
    "normalize_cyrillic",
    "normalize_time",
    "parse_group_slot",
    "schedule_api_adapter",
    "smart_search_service",
    "split_subject_text",
    "table_image_service",
    "teacher_directory_service",
    "wrap_groups_to_lines",
]
