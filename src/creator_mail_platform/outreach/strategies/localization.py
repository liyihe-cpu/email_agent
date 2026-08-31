from __future__ import annotations

import re


LANGUAGE_NAMES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "ko": "Korean",
    "ja": "Japanese",
    "ru": "Russian",
    "ar": "Arabic",
}

LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "english": "en",
    "es": "es", "sp": "es", "spa": "es", "spanish": "es",
    "pt": "pt", "ptbr": "pt", "pt-br": "pt", "portuguese": "pt",
    "fr": "fr", "fre": "fr", "french": "fr",
    "de": "de", "ger": "de", "german": "de",
    "ko": "ko", "korean": "ko",
    "ja": "ja", "japanese": "ja",
    "ru": "ru", "russian": "ru",
    "ar": "ar", "ar-xa": "ar", "arabic": "ar",
}


def resolve_language_code(raw_language: str | None) -> str:
    if not raw_language:
        return "en"
    tokens = [token.strip().lower() for token in re.split(r"[、,+/;|]", raw_language)]
    resolved = [LANGUAGE_ALIASES[token] for token in tokens if token in LANGUAGE_ALIASES]
    non_english = [code for code in resolved if code != "en"]
    return non_english[0] if non_english else (resolved[0] if resolved else "en")
