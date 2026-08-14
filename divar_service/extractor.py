from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from divar_service.config.aliases import (
    BRAND_ALIASES,
    CONTEXT_ONLY_TRIMS,
    CONTEXTUAL_TRIM_ALIASES,
    MODEL_ALIASES,
    TRIM_ALIASES,
)
from divar_service.config.vehicle_catalog import (
    VEHICLE_CATALOG,
    get_models_for_brand,
    get_trims,
    is_valid_vehicle,
)
from divar_service.normalizer import (
    build_vehicle_key,
    find_alias,
    normalize_for_match,
    normalize_whitespace,
    normalize_year,
)


@dataclass(frozen=True, slots=True)
class ExtractedVehicle:
    brand: str
    model: str
    trim: str
    year: int
    mileage: int | None

    @property
    def vehicle_key(
        self,
    ) -> tuple[str, str, str, int]:
        return build_vehicle_key(
            brand=self.brand,
            model=self.model,
            trim=self.trim,
            year=self.year,
        )


def extract_vehicle(
    title: object,
    description: object = "",
    structured_brand: object = "",
    structured_model: object = "",
    structured_trim: object = "",
    structured_year: object = None,
) -> ExtractedVehicle | None:
    """
    Extract an exact vehicle classification.

    Brand + model + year are always required.

    Trim is required when the vehicle catalog defines trims
    for that brand/model. Vehicles with no catalog trims use
    an empty trim string.
    """
    title_text = normalize_whitespace(
        title
    )
    description_text = normalize_whitespace(
        description
    )

    combined_text = normalize_whitespace(
        f"{title_text} {description_text}"
    )

    if not combined_text:
        return None

    brand = _extract_brand(
        combined_text,
        structured_brand,
    )

    model = _extract_model(
        text=combined_text,
        brand=brand,
        structured_model=structured_model,
    )

    if not model:
        return None

    if not brand:
        brand = _infer_unique_brand(
            model
        )

    if not brand:
        return None

    if model not in get_models_for_brand(
        brand
    ):
        return None

    year = normalize_year(
        structured_year
    )

    if year is None:
        year = _extract_year_from_text(
            combined_text
        )

    if year is None:
        return None

    trim = _extract_trim(
        title=title_text,
        brand=brand,
        model=model,
        structured_trim=structured_trim,
    )

    available_trims = get_trims(
        brand,
        model,
    )

    if available_trims and not trim:
        return None

    if not is_valid_vehicle(
        brand=brand,
        model=model,
        trim=trim,
    ):
        return None

    mileage = _extract_mileage(
        combined_text
    )

    return ExtractedVehicle(
        brand=brand,
        model=model,
        trim=trim,
        year=year,
        mileage=mileage,
    )


def _extract_mileage(
    text: str,
) -> int | None:
    """
    Extract mileage from advertisement text.
    """
    normalized_text = normalize_for_match(
        text
    )

    if not normalized_text:
        return None

    if re.search(
        r"کارکرد\s*[:：]?\s*صفر",
        normalized_text,
    ):
        return 0

    patterns = (
        r"کارکرد\s*[:：]?\s*"
        r"([0-9][0-9,،٬.\s]{0,15})"
        r"\s*(?:کیلومتر|کیلو|km)?",

        r"([0-9][0-9,،٬.\s]{2,15})"
        r"\s*(?:کیلومتر|کیلو|km)",
    )

    for pattern in patterns:
        match = re.search(
            pattern,
            normalized_text,
            re.IGNORECASE,
        )

        if not match:
            continue

        raw = match.group(1)

        raw = (
            raw
            .replace(",", "")
            .replace("،", "")
            .replace("٬", "")
            .replace(".", "")
            .replace(" ", "")
        )

        if not raw.isdigit():
            continue

        value = int(raw)

        if 0 <= value <= 2_000_000:
            return value

    return None


def _extract_brand(
    text: str,
    structured_brand: object,
) -> str | None:
    structured_value = _canonical_from_values(
        structured_brand,
        _all_brands(),
    )

    if structured_value:
        return structured_value

    alias_match = find_alias(
        structured_brand,
        BRAND_ALIASES,
    )

    if alias_match:
        return alias_match

    return find_alias(
        text,
        BRAND_ALIASES,
    )


def _extract_model(
    text: str,
    brand: str | None,
    structured_model: object,
) -> str | None:
    valid_models = (
        get_models_for_brand(brand)
        if brand
        else _all_models()
    )

    structured_value = _canonical_from_values(
        structured_model,
        valid_models,
    )

    if structured_value:
        return structured_value

    model_aliases = _filter_aliases_by_values(
        MODEL_ALIASES,
        valid_models,
    )

    alias_match = find_alias(
        structured_model,
        model_aliases,
    )

    if alias_match:
        return alias_match

    return find_alias(
        text,
        model_aliases,
    )


def _extract_trim(
    title: str,
    brand: str,
    model: str,
    structured_trim: object,
) -> str:
    """
    Extract trim conservatively from the advertisement title.

    The full card text is intentionally not used here because
    prices and other numeric metadata can resemble numeric trims.
    """
    valid_trims = get_trims(
        brand,
        model,
    )

    if not valid_trims:
        return ""

    structured_value = _canonical_from_values(
        structured_trim,
        valid_trims,
    )

    if structured_value:
        return structured_value

    trim_aliases = _filter_aliases_by_values(
        TRIM_ALIASES,
        valid_trims,
    )

    alias_match = find_alias(
        structured_trim,
        trim_aliases,
    )

    if alias_match:
        return alias_match

    contextual_aliases = (
        CONTEXTUAL_TRIM_ALIASES.get(
            (brand, model),
            {},
        )
    )

    contextual_match = find_alias(
        title,
        contextual_aliases,
    )

    if contextual_match:
        return contextual_match

    # Add exact canonical trim names only when they are not
    # too broad, too short, or purely numeric.
    safe_aliases = dict(
        trim_aliases
    )

    for trim in valid_trims:
        if trim in CONTEXT_ONLY_TRIMS:
            continue

        safe_aliases[trim] = trim

    alias_match = find_alias(
        title,
        safe_aliases,
    )

    return alias_match or ""


def _extract_year_from_text(
    text: object,
) -> int | None:
    normalized_text = normalize_for_match(
        text
    )

    if not normalized_text:
        return None

    labeled_patterns = (
        r"(?:مدل|سال)\s*[:\-]?\s*(\d{2,4})",
        r"(\d{2,4})\s*(?:مدل|سال)",
    )

    for pattern in labeled_patterns:
        matches = re.findall(
            pattern,
            normalized_text,
        )

        for match in matches:
            year = normalize_year(
                match
            )

            if year is not None:
                return year

    four_digit_matches = re.findall(
        r"(?<!\d)(\d{4})(?!\d)",
        normalized_text,
    )

    for match in four_digit_matches:
        year = normalize_year(
            match
        )

        if year is not None:
            return year

    return None


def _infer_unique_brand(
    model: str,
) -> str | None:
    matching_brands = {
        vehicle.brand
        for vehicle in VEHICLE_CATALOG
        if vehicle.model == model
    }

    if len(matching_brands) != 1:
        return None

    return next(
        iter(matching_brands)
    )


def _canonical_from_values(
    value: object,
    valid_values: tuple[str, ...],
) -> str | None:
    normalized_value = normalize_for_match(
        value
    )

    if not normalized_value:
        return None

    matches = [
        canonical_value
        for canonical_value in valid_values
        if normalize_for_match(
            canonical_value
        ) == normalized_value
    ]

    if len(matches) != 1:
        return None

    return matches[0]


def _filter_aliases_by_values(
    aliases: Mapping[str, str],
    valid_values: tuple[str, ...],
) -> dict[str, str]:
    valid_set = set(
        valid_values
    )

    return {
        alias: canonical_value
        for alias, canonical_value
        in aliases.items()
        if canonical_value in valid_set
    }


def _all_brands() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            vehicle.brand
            for vehicle in VEHICLE_CATALOG
        )
    )


def _all_models() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            vehicle.model
            for vehicle in VEHICLE_CATALOG
        )
    )
