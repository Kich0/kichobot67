"""Unit tests for the i18n translation engine."""

from pathlib import Path
import pytest
from src.core.i18n import I18n, i18n


def test_i18n_loads_actual_locales():
    """Tests that the global i18n instance loaded existing project locales."""
    # Check that common keys exist in Russian
    ru_antispam = i18n.get("antispam", lang="ru")
    assert "Не спамь" in ru_antispam

    # Check key exists in Kazakh
    kz_antispam = i18n.get("antispam", lang="kz")
    assert kz_antispam is not None and kz_antispam != "antispam"


def test_interpolation_double_brackets():
    """Tests interpolation with {{var}} syntax."""
    text = i18n.get("department", lang="ru", departmentName="Информатика")
    assert "Информатика" in text
    assert "{{departmentName}}" not in text


def test_language_fallback():
    """Tests that missing Kazakh key falls back to Russian."""
    custom_i18n = I18n()
    custom_i18n._translations = {
        "ru": {"only_in_ru": "Только по-русски: {{val}}"},
        "kz": {},
    }
    result = custom_i18n.get("only_in_ru", lang="kz", val="123")
    assert result == "Только по-русски: 123"


def test_missing_key_returns_key():
    """Tests that completely missing key returns the key itself or default."""
    assert i18n.get("non_existent_key_xyz", lang="ru") == "non_existent_key_xyz"
    assert i18n.get("non_existent_key_xyz", lang="ru", default="fallback") == "fallback"


def test_language_normalization():
    """Tests that language aliases map correctly."""
    assert I18n.normalize_lang("kk") == "kz"
    assert I18n.normalize_lang("kaz") == "kz"
    assert I18n.normalize_lang("KZ") == "kz"
    assert I18n.normalize_lang("ru") == "ru"
    assert I18n.normalize_lang("rus") == "ru"
    assert I18n.normalize_lang(None) == "ru"
    assert I18n.normalize_lang("unknown") == "ru"
