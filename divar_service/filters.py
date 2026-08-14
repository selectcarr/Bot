from __future__ import annotations

from dataclasses import dataclass

from divar_service.normalizer import (
    normalize_for_match,
    normalize_price,
)


BLOCKED_PHRASES = (
    "اقساط",
    "اقساطی",
    "قسطی",
    "فروش قسطی",
    "لیزینگ",
    "پیش پرداخت",
    "پیش‌پرداخت",
    "بدون پیش پرداخت",
    "بدون پیش‌پرداخت",
    "تحویل اقساطی",
    "فروش شرایطی",
    "شرایط ویژه پرداخت",
    "حواله",
    "فروش حواله",
    "قیمت توافقی",
    "توافقی",
    "برای اطلاع از قیمت",
    "تماس بگیرید",
    "تماس برای قیمت",
    "قیمت تماس",
    "قیمت در تماس",
    "قیمت نامشخص",
    "قیمت مشخص نیست",
    "قیمت صفر",
)


@dataclass(frozen=True)
class FilterResult:
    accepted: bool
    reason: str = ""


def contains_blocked_phrase(
    title: object,
    description: object = "",
) -> str | None:
    """
    Return the blocked phrase found in title or description.
    """
    combined_text = normalize_for_match(
        f"{title or ''} {description or ''}"
    )

    for phrase in BLOCKED_PHRASES:
        normalized_phrase = normalize_for_match(
            phrase
        )

        if normalized_phrase in combined_text:
            return phrase

    return None


def validate_ad(
    title: object,
    description: object,
    price: object,
) -> FilterResult:
    """
    Apply the first-stage advertisement filters.
    """
    normalized_title = normalize_for_match(
        title
    )

    if not normalized_title:
        return FilterResult(
            accepted=False,
            reason="missing_title",
        )

    blocked_phrase = contains_blocked_phrase(
        title,
        description,
    )

    if blocked_phrase:
        return FilterResult(
            accepted=False,
            reason=(
                f"blocked_phrase:{blocked_phrase}"
            ),
        )

    normalized_price = normalize_price(
        price
    )

    if normalized_price is None:
        return FilterResult(
            accepted=False,
            reason="invalid_price",
        )

    return FilterResult(
        accepted=True,
        reason="accepted",
    )


def is_acceptable_ad(
    title: object,
    description: object,
    price: object,
) -> bool:
    return validate_ad(
        title=title,
        description=description,
        price=price,
    ).accepted
