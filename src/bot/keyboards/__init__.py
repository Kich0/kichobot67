"""Telegram inline and reply keyboards package."""

from src.bot.keyboards.inline import (
    get_departments_keyboard,
    get_faculties_keyboard,
    get_fuzzy_suggestions_keyboard,
    get_groups_keyboard,
    get_language_keyboard,
    get_new_schedule_keyboard,
    get_programs_keyboard,
    get_schedule_pagination_keyboard,
    get_search_results_keyboard,
    get_teacher_table_keyboard,
    get_teacher_text_keyboard,
    get_teachers_keyboard,
)

__all__ = [
    "get_departments_keyboard",
    "get_faculties_keyboard",
    "get_fuzzy_suggestions_keyboard",
    "get_groups_keyboard",
    "get_language_keyboard",
    "get_new_schedule_keyboard",
    "get_programs_keyboard",
    "get_schedule_pagination_keyboard",
    "get_search_results_keyboard",
    "get_teacher_table_keyboard",
    "get_teacher_text_keyboard",
    "get_teachers_keyboard",
]
