"""Lightweight Internationalization (i18n) module for Russian and Kazakh.

Loads translations from JSON locale files (bot/locales/*.json) without heavy
third-party dependencies. Supports template interpolation ({{var}} and {var}),
language fallback (KZ -> RU), and language code normalization.
"""

from pathlib import Path
import re
from typing import Any
import orjson


class I18n:
    """Translation manager for RU/KZ locales."""

    DEFAULT_LANG = "ru"
    SUPPORTED_LANGUAGES = ("ru", "kz")

    def __init__(self, locales_dir: Path | str | None = None) -> None:
        self._translations: dict[str, dict[str, str]] = {}
        if locales_dir:
            self.load_from_dir(Path(locales_dir))
        else:
            # Check locales directories in priority order:
            repo_root = Path(__file__).resolve().parent.parent.parent
            for candidate in [repo_root / "locales", repo_root / "bot" / "locales", repo_root / "data" / "locales"]:
                if candidate.is_dir():
                    self.load_from_dir(candidate)
                    break

    def load_from_dir(self, directory: Path) -> None:
        """Loads all .json files in the specified directory into memory."""
        for lang in self.SUPPORTED_LANGUAGES:
            file_path = directory / f"{lang}.json"
            if file_path.is_file():
                try:
                    with open(file_path, "rb") as f:
                        data = orjson.loads(f.read())
                        if isinstance(data, dict):
                            self._translations[lang] = {str(k): str(v) for k, v in data.items()}
                except Exception as e:
                    # Keep existing or empty on load error
                    self._translations.setdefault(lang, {})
            else:
                self._translations.setdefault(lang, {})

    @staticmethod
    def normalize_lang(lang: str | None) -> str:
        """Normalizes language code (e.g., 'kk', 'kz', 'kaz' -> 'kz', default 'ru')."""
        if not lang:
            return I18n.DEFAULT_LANG
        code = lang.lower().strip()
        if code in ("kz", "kk", "kaz", "kazakh", "қаз", "каз"):
            return "kz"
        return "ru"

    def get(self, key: str, lang: str = "ru", default: str | None = None, **kwargs: Any) -> str:
        """Retrieves and interpolates a localized string.

        Args:
            key: Translation key (e.g. 'department')
            lang: Target language code ('ru' or 'kz')
            default: Default string if key is completely missing (defaults to key)
            **kwargs: Interpolation parameters for {{var}} or {var} placeholders
        """
        normalized_lang = self.normalize_lang(lang)

        # 1. Try target language
        text = self._translations.get(normalized_lang, {}).get(key)

        # 2. Fallback to default (RU) if missing in KZ
        if text is None and normalized_lang != self.DEFAULT_LANG:
            text = self._translations.get(self.DEFAULT_LANG, {}).get(key)

        # 3. Fallback to default arg or key itself
        if text is None:
            text = default if default is not None else key

        # 4. Interpolate variables (supports both {{var}} and {var})
        if kwargs and text:
            for k, v in kwargs.items():
                val_str = str(v)
                text = text.replace(f"{{{{{k}}}}}", val_str).replace(f"{{{k}}}", val_str)

        return text

    def __call__(self, key: str, lang: str = "ru", default: str | None = None, **kwargs: Any) -> str:
        return self.get(key, lang=lang, default=default, **kwargs)


# Global singleton instance
i18n = I18n()
t = i18n.get

__all__ = ["I18n", "i18n", "t"]
