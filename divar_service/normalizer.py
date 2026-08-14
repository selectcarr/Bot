from __future__ import annotations

import math
import re
from typing import Mapping


PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ENGLISH_DIGITS = "0123456789"

DIGIT_TRANSLATION = str.maketrans(
    PERSIAN_DIGITS + ARABIC_DIGITS,
    ENGLISH_DIGITS + ENGLISH_DIGITS,
)

INTEGER_SEPARATORS_PATTERN = re.compile(
    r"[\s,،٬._]+"
)

WHITESPACE_PATTERN = re.compile(r"\s+")

# A Unicode letter: a word character that is neither
# a digit nor an underscore.
LETTER_CHARACTER = r"[^\W\d_]"


def normalize_digits(value: object) -> str:
    """
    Convert Persian and Arabic digits to English digits.
    """
    if value is None:
        return ""

    return str(value).translate(
        DIGIT_TRANSLATION
    )


def normalize_whitespace(value: object) -> str:
    """
    Remove extra spaces and normalize Persian half-spaces.
    """
    text = normalize_digits(value)

    text = text.replace("\u200c", " ")
    text = text.replace("\u200f", " ")
    text = text.replace("\u200e", " ")

    return WHITESPACE_PATTERN.sub(
        " ",
        text,
    ).strip()


def normalize_for_match(value: object) -> str:
    """
    Prepare text for conservative brand/model/trim matching.

    Letter/digit boundaries are separated so strings such as
    "405تیوفایو", "مدل1402" and "تیپ5" can be matched safely.
    """
    text = normalize_whitespace(value).lower()

    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "_": " ",
        "/": " ",
        "\\": " ",
        "|": " ",
        "–": "-",
        "—": "-",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(
        r"[()\[\]{}:؛;،,!?؟]+",
        " ",
        text,
    )

    text = re.sub(
        (
            rf"(?<={LETTER_CHARACTER})(?=\d)"
            rf"|(?<=\d)(?={LETTER_CHARACTER})"
        ),
        " ",
        text,
    )

    return normalize_whitespace(text)


def parse_integer(value: object) -> int | None:
    """
    Parse one non-negative integer conservatively.

    Text containing labels or multiple unrelated numeric fields
    is rejected instead of concatenating every digit in the text.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value if value >= 0 else None

    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None

        integer_value = int(value)
        return integer_value if integer_value >= 0 else None

    text = normalize_digits(value).strip()

    if not text or text.startswith("-"):
        return None

    if not re.fullmatch(
        r"\+?[0-9\s,،٬._]+",
        text,
    ):
        return None

    if text.startswith("+"):
        text = text[1:]

    cleaned = INTEGER_SEPARATORS_PATTERN.sub(
        "",
        text,
    )

    if not cleaned.isdigit():
        return None

    try:
        result = int(cleaned)
    except ValueError:
        return None

    return result if result >= 0 else None


def normalize_price(value: object) -> int | None:
    """
    Normalize a vehicle price into an integer.

    Prices equal to zero are rejected.
    """
    price = parse_integer(value)

    if price is None or price <= 0:
        return None

    return price


def normalize_year(value: object) -> int | None:
    """
    Normalize year according to project rules.

    Examples:
        97   -> 1397
        402  -> 1402
        1402 -> 1402
        2013 -> 2013

    No Gregorian-to-Solar conversion is performed.
    """
    year = parse_integer(value)

    if year is None:
        return None

    if 0 <= year <= 99:
        return year + 1300

    if 100 <= year <= 999:
        return year + 1000

    if 1300 <= year <= 1499:
        return year

    if 2000 <= year <= 2100:
        return year

    return None


def find_alias(
    text: object,
    aliases: Mapping[str, str],
) -> str | None:
    """
    Find the longest matching alias in text.

    Longest match is used to avoid choosing a shorter,
    incorrect model or trim.
    """
    normalized_text = normalize_for_match(text)

    if not normalized_text:
        return None

    matches: list[tuple[int, str]] = []

    for alias, canonical_value in aliases.items():
        normalized_alias = normalize_for_match(alias)

        if not normalized_alias:
            continue

        pattern = (
            r"(?<![\w])"
            + re.escape(normalized_alias)
            + r"(?![\w])"
        )

        if re.search(pattern, normalized_text):
            matches.append(
                (
                    len(normalized_alias),
                    canonical_value,
                )
            )

    if not matches:
        return None

    matches.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return matches[0][1]


def build_vehicle_key(
    brand: object,
    model: object,
    trim: object,
    year: object,
) -> tuple[str, str, str, int]:
    """
    Build the canonical market-comparison key:

        brand + model + trim + year
    """
    normalized_brand = normalize_for_match(
        brand
    )
    normalized_model = normalize_for_match(
        model
    )
    normalized_trim = normalize_for_match(
        trim
    )

    if not normalized_brand:
        raise ValueError(
            "brand cannot be empty."
        )

    if not normalized_model:
        raise ValueError(
            "model cannot be empty."
        )

    normalized_year = normalize_year(
        year
    )

    if normalized_year is None:
        raise ValueError(
            "year is invalid."
        )

    return (
        normalized_brand,
        normalized_model,
        normalized_trim,
        normalized_year,
    )
