#!/usr/bin/env python3
"""Offline parsing of advertisement text. No fetching and no message sending.

API v2: normalize_source_text(source, raw_text) returns SourceNormalization.
Prices are integer toman; Gregorian model years stay Gregorian. Missing or
ambiguous values are not guessed. Telegram shorthand is accepted ONLY directly
after a price label. These field checks do not constitute a deal decision or a
physical inspection. Body labels describe claims in the supplied text.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Optional

API_VERSION = 2
MIN_PRICE = 20_000_000
MAX_PRICE = 100_000_000_000
FINANCE_COMPONENT_PHRASES = (
    "پیش پرداخت", "پیشپرداخت", "مبلغ اولیه", "پرداخت اولیه",
    "ودیعه", "قسط ماهانه", "پرداخت ماهانه", "مبلغ قسط",
    "قسط", "اقساط", "اقساطی", "قسطی", "لیزینگ", "وام",
    "حواله", "پیش فروش", "ثبت نام", "ثبتنام", "چکی",
    "اعتباری", "شرایطی", "معاوضه", "تهاتر",
)

# Mere presence of financing/transaction words is not enough to reject a listing.
# They become a blocker only when no trustworthy full-vehicle price can be read,
# or when a monetary expression is explicitly tied to a finance component.
FINANCE_AMOUNT_PREFIX = re.compile(
    r"(?:پیش\s*پرداخت|پیشپرداخت|مبلغ\s*اولیه|پرداخت\s*اولیه|ودیعه|"
    r"قسط(?:\s*ماهانه)?|پرداخت\s*ماهانه|مبلغ\s*قسط|وام|حواله)"
    r"[^\n]{0,24}$", re.I
)
FINANCE_AMOUNT_SUFFIX = re.compile(
    r"^\s*(?:تومان|تومن|ریال|میلیون|ملیون|میلیارد|ملیارد)?"
    r"\s*(?:قسط|اقساط|ماهانه|پیش\s*پرداخت|پیشپرداخت|ودیعه|حواله)\b", re.I
)
_NUM = r"[0-9]+(?:[,./][0-9]+)*"
_NUMBER = re.compile(_NUM)
_UNIT = re.compile(r"\s*(میلیارد|ملیارد|billion|میلیون|ملیون|million|تومان|تومن|ریال)(?![آ-یa-z])", re.I)
_LABEL = re.compile(r"(?<![آ-یa-z])(?:قیمت(?:\s+(?:نقدی|کل(?:\s+خودرو)?|خودرو|فروش))?|مبلغ(?:\s+(?:کل|خودرو|فروش))?)(?![آ-یa-z])", re.I)
_YEAR_LABEL = re.compile(r"(?<![آ-یa-z])(?:مدل|سال\s*(?:ساخت|تولید)?)[\s:：=\-]*(\d{2}|\d{4})(?!\d)", re.I)
_YEAR4 = re.compile(r"(?<![\w.])(?:13\d{2}|14\d{2}|19[5-9]\d|20[0-3]\d)(?![\w.])")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True)
class SourceNormalization:
    source: str
    raw_text: str
    normalized_text: str
    price: Optional[int]
    model_year: Optional[int]
    body_condition: str
    finance_blocked: bool
    price_reason: str
    year_reason: str
    mileage: Optional[int] = None
    mileage_reason: str = "unknown_mileage"

    @property
    def can_enter_market_history(self) -> bool:
        # Field availability only; the collector must also validate identity,
        # trim, mileage, body, provenance, and duplicates before using a sample.
        return not self.finance_blocked and self.price is not None and self.model_year is not None

    @property
    def can_be_confirmed_deal(self) -> bool:
        # Compatibility helper, NOT permission to send a Telegram notification.
        return self.can_enter_market_history and self.body_condition != "unknown"


def normalize_digits(value: str) -> str:
    return str(value or "").translate(_DIGITS)


def normalize_text(value: str) -> str:
    value = normalize_digits(unicodedata.normalize("NFKC", str(value or "")))
    value = value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    value = re.sub(r"[\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", value)
    value = value.replace("٬", ",").replace("٫", ".").replace("،", ",")
    value = re.sub(r"[^\S\n]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def contains_finance_phrase(text: str) -> bool:
    """Return whether the text mentions financing/transaction options.

    This is diagnostic metadata only. A mention by itself is NOT permission to
    reject a listing or discard a valid full-vehicle price.
    """
    normalized = normalize_text(text).casefold()
    return any(normalize_text(phrase) in normalized for phrase in FINANCE_COMPONENT_PHRASES)


def _finance_amount_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 80):start]
    after = text[end:min(len(text), end + 80)]
    return bool(FINANCE_AMOUNT_PREFIX.search(before) or FINANCE_AMOUNT_SUFFIX.search(after))


def _number(raw: str, *, scaled: bool = False) -> Optional[Decimal]:
    """Do not conflate decimal punctuation and a grouped full currency value."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
        raw = raw.replace(",", "")
    elif "," in raw:
        return None
    if not scaled and re.fullmatch(r"\d{1,3}(?:[./]\d{3}){2,}", raw):
        raw = raw.replace(".", "").replace("/", "")
    elif not scaled and re.fullmatch(r"\d{1,3}\.\d{3}", raw):
        raw = raw.replace(".", "")
    elif "/" in raw:
        if not scaled or raw.count("/") != 1:
            return None
        raw = raw.replace("/", ".")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _valid_price(value: Optional[Decimal]) -> Optional[int]:
    if value is None or value != value.to_integral_value():
        return None
    return int(value) if MIN_PRICE <= value <= MAX_PRICE else None


def _read_money(text: str, *, shorthand: bool) -> tuple[Optional[int], str, int]:
    """Read one money expression starting at text[0], never a preceding car ID."""
    prefix = re.match(r"[\s:：=💰💵💲💸]*", text).end()
    match = _NUMBER.match(text, prefix)
    if not match:
        return None, "missing_price_value", 0
    raw = match.group()
    # Unformatted phone numbers must not become vehicle prices.
    if raw.startswith("0") and raw.isdigit() and len(raw) >= 7:
        return None, "phone_like_price", match.end()
    unit = _UNIT.match(text, match.end())
    end = match.end()
    amount = None
    reason = "explicit_unit_price"
    if unit:
        name = unit.group(1).casefold()
        end = unit.end()
        scale = (Decimal(1_000_000_000) if name in {"میلیارد", "ملیارد", "billion"}
                 else Decimal(1_000_000) if name in {"میلیون", "ملیون", "million"}
                 else Decimal("0.1") if name == "ریال" else Decimal(1))
        number = _number(raw, scaled=scale >= 1_000_000)
        amount = number * scale if number is not None else None
        if scale == 1_000_000_000:
            more = re.match(r"\s*و\s*(" + _NUM + r")\s*(?:میلیون|ملیون|million)(?![آ-یa-z])", text[end:], re.I)
            if more:
                extra = _number(more.group(1), scaled=True)
                if extra is None or not Decimal(0) <= extra < 1000:
                    return None, "ambiguous_composite_price", end
                amount = amount + extra * 1_000_000 if amount is not None else None
                end += more.end()
            elif re.match(r"\s*و\s*\d", text[end:]):
                return None, "ambiguous_composite_price", end
        if scale >= 1_000_000:
            currency = re.match(r"\s*(تومان|تومن|ریال)(?![آ-ی])", text[end:])
            if currency:
                if currency.group(1) == "ریال" and amount is not None:
                    amount /= 10
                end += currency.end()
    else:
        if not shorthand:
            number = _number(raw)
            amount = number
            reason = "labeled_full_price"
        elif re.fullmatch(r"\d{1,2}[./]\d{3}", raw):
            amount = Decimal(raw.replace("/", ".")) * 1_000_000_000
            reason = "labeled_billion_shorthand"
        else:
            amount = _number(raw)
            reason = "labeled_full_price"
            if amount is not None and amount == amount.to_integral_value() and 100 <= amount <= 99_999:
                amount *= 1_000_000
                reason = "labeled_million_shorthand"
    if re.match(r"\s*(?:هزار|دلار|یورو|درهم|usd|eur|aed)(?![آ-یa-z])", text[end:], re.I):
        return None, "unsupported_price_unit", end
    if re.match(r"\s*(?:تا|الی|یا|و|[-–])\s*\d", text[end:]):
        return None, "ambiguous_price_range", end
    if end < len(text) and re.match(r"[a-zA-Z0-9/.,]", text[end]):
        return None, "ambiguous_price_token", end
    return _valid_price(amount), reason if _valid_price(amount) is not None else "invalid_price", end


def parse_vehicle_price(text: str, *, allow_shorthand: bool = True) -> tuple[Optional[int], str]:
    text = normalize_text(text)
    if re.search(r"توافقی|قیمت\s*(?:در\s*)?تماس", text):
        return None, "non_cash_price"
    results: list[tuple[int, str]] = []
    labels = list(_LABEL.finditer(text))
    if labels:
        for label in labels:
            value, reason, _ = _read_money(text[label.end():], shorthand=allow_shorthand)
            if value is None:
                return None, reason
            results.append((value, reason))
    else:
        # Without a price label, require an explicit monetary unit. Never take
        # the largest number: it might be a phone number, mileage, or post ID.
        for match in _NUMBER.finditer(text):
            if not _UNIT.match(text, match.end()):
                continue
            if text[:match.start()].rstrip().endswith(("-", "−")):
                return None, "negative_price"
            line_start = text.rfind("\n", 0, match.start()) + 1
            prefix = text[line_start:match.start()]
            if re.search(r"هزینه|تعمیر|بیمه|تخفیف|شماره|تماس|کارکرد|مدل", prefix):
                continue
            if _finance_amount_context(text, match.start(), match.end()):
                continue
            # A component of a composite expression is consumed with its first
            # component rather than becoming a second, conflicting price.
            if results and match.start() < consumed_end:
                continue
            value, reason, consumed = _read_money(text[match.start():], shorthand=False)
            if value is None:
                return None, reason
            consumed_end = match.start() + consumed
            results.append((value, reason))
    if not results:
        # Public web listing cards (notably Sheypoor) may render the complete
        # vehicle price as a grouped integer such as 975,000,000 without a
        # nearby price label or currency word. Accept only this highly
        # constrained shape: at least two thousands separators, one unique
        # plausible full-vehicle amount, and no finance/component context.
        # This deliberately does NOT accept ungrouped 7-12 digit numbers, so
        # phone numbers, ad IDs and ordinary mileage cannot become prices.
        grouped: list[int] = []
        for line in text.splitlines() or [text]:
            for match in re.finditer(r"(?<![\d,])(\d{1,3}(?:,\d{3}){2,3})(?![\d,])", line):
                if line[:match.start()].rstrip().endswith(("-", "−")):
                    continue
                prefix = line[:match.start()]
                if re.search(r"(?:هزینه|تعمیر|بیمه|تخفیف|شماره|تماس|کارکرد|پیمایش|مدل|سال)\s*[:=\-]?\s*$", prefix):
                    continue
                # Reconstruct the absolute span for the existing finance-context
                # guard, which handles prepayment/deposit/installment wording.
                line_start = text.find(line)
                absolute_start = line_start + match.start() if line_start >= 0 else match.start()
                absolute_end = line_start + match.end() if line_start >= 0 else match.end()
                if _finance_amount_context(text, absolute_start, absolute_end):
                    continue
                value = _valid_price(_number(match.group(1), scaled=False))
                if value is not None:
                    grouped.append(value)
        unique_grouped = sorted(set(grouped))
        if len(unique_grouped) == 1:
            return unique_grouped[0], "unlabeled_grouped_full_price"
        if len(unique_grouped) > 1:
            return None, "ambiguous_multiple_prices"
        return None, "invalid_price"
    if len({price for price, _ in results}) != 1:
        return None, "ambiguous_multiple_prices"
    return results[0]


def _normalize_year_number(year: int) -> Optional[int]:
    if 1300 <= year <= 1499 or 1950 <= year <= 2035:
        return year
    if 0 <= year <= 30:
        return 1400 + year
    if 70 <= year <= 99:
        return 1300 + year
    return None


def parse_model_year(text: str) -> tuple[Optional[int], str]:
    text = normalize_text(text)
    labeled = [_normalize_year_number(int(m.group(1))) for m in _YEAR_LABEL.finditer(text)]
    if labeled:
        if any(value is None for value in labeled):
            return None, "invalid_year"
        if len(set(labeled)) != 1:
            return None, "ambiguous_year"
        return labeled[0], "labeled_year"
    years = []
    for line in text.splitlines():
        if re.search(r"قیمت|مبلغ|کارکرد|کیلومتر|تومان|میلیون|میلیارد|تلفن|شماره|حجم|سی\s*سی", line):
            continue
        # These are model names / engine designations, not model years.
        line = re.sub(r"(?:پژو|peugeot)\s*2008|(?:سراتو|cerato)\s*2000", "", line, flags=re.I)
        for match in _YEAR4.finditer(line):
            year = _normalize_year_number(int(match.group()))
            if year is not None:
                years.append(year)
    if len(set(years)) > 1:
        return None, "ambiguous_year"
    return (years[0], "four_digit_year") if years else (None, "invalid_year")


def parse_mileage(text: str) -> tuple[Optional[int], str]:
    text = normalize_text(text)
    values = []
    for match in re.finditer(r"(?:کارکرد|پیمایش)[\s:=\-]*(" + _NUM + r")(?:\s*(هزار))?", text):
        raw = match.group(1)
        n = _number(raw, scaled=bool(match.group(2)))
        if n is None:
            return None, "invalid_mileage"
        n *= 1000 if match.group(2) else 1
        if n != n.to_integral_value() or not 0 <= n <= 2_000_000:
            return None, "invalid_mileage"
        values.append(int(n))
    if not values:
        for match in re.finditer(r"(" + _NUM + r")\s*(هزار)?\s*(?:کیلومتر|کیلو|km)(?![آ-یa-z])", text, re.I):
            n = _number(match.group(1), scaled=bool(match.group(2)))
            if n is not None:
                n *= 1000 if match.group(2) else 1
                if n == n.to_integral_value() and 0 <= n <= 2_000_000:
                    values.append(int(n))
    zero = bool(re.search(r"(?:کارکرد\s*صفر|صفر\s*کیلومتر)", text))
    if zero and not re.search(r"در\s*حد\s*صفر|مانند\s*صفر|مثل\s*صفر", text):
        values.append(0)
    if len(set(values)) > 1:
        return None, "conflicting_mileage"
    return (values[0], "explicit_mileage") if values else (None, "unknown_mileage")


def classify_body_condition(text: str) -> str:
    text = normalize_text(text).casefold()
    clean = bool(re.search(r"بدون\s*رنگ|بی\s*رنگ|بیرنگ|رنگ\s*شدگی\s*ندارد|رنگشدگی\s*ندارد", text))
    # Remove explicit negations BEFORE looking for positive damage claims.
    positive = re.sub(r"بدون\s*(?:هیچ\s*گونه\s*|هرگونه\s*)?(?:تصادف|رنگ\s*شدگی|رنگشدگی|تعویض(?:\s*قطعه)?)", " ", text)
    positive = re.sub(r"(?:تصادفی?|رنگ\s*شدگی|رنگشدگی|تعویض(?:\s*قطعه)?)\s*(?:ندارد|نیست|نشده)", " ", positive)
    positive = re.sub(r"(?:شاسی|ستون|سقف)\s*(?:ضربه|آسیب)\s*(?:ندارد|نخورده|ندیده)", " ", positive)
    if re.search(r"(?:شاسی|ستون|سقف|اتاق)(?:\s+(?:جلو|عقب|راست|چپ|ها|خودرو)){0,3}\s*(?:ضربه|آسیب|جوش|تعویض|سالم\s*نیست)", positive):
        return "structural"
    if re.search(r"(?:ضربه|آسیب|جوش|تعویض)(?:\s+(?:به|در)){0,1}\s*(?:شاسی|ستون|سقف|اتاق)", positive):
        return "structural"
    if re.search(r"تصادف|چپی|واژگون|ضربه\s*شدید", positive):
        return "accident"
    part = r"(?:درب|در|گلگیر|کاپوت|صندوق|سینی)"
    qualifiers = r"(?:\s+(?:جلو|عقب|راست|چپ|سمت|خودرو|یک|دو|1|2)){0,4}"
    if re.search(part + qualifiers + r"\s*تعویض(?!\s*(?:نشده|نیست))|تعویض\s*" + part + r"|قطعه\s*تعویضی", positive):
        return "replaced"
    if re.search(r"(?:تمام|کامل|دور)\s*رنگ", positive):
        return "full_paint"
    if re.search(r"(?:دو|سه|چهار|پنج|[2-9]|چند)\s*(?:تکه|لکه|قطعه)\s*رنگ", positive):
        return "multi_paint"
    if re.search(r"(?:یک|یه|1|تک)\s*(?:تکه|لکه|قطعه)\s*رنگ", positive):
        return "minor_paint"
    painted_parts = re.findall(part + qualifiers + r"\s*(?:رنگ\s*(?:دارد|شده)?|رنگی)", positive)
    if painted_parts:
        return "minor_paint" if len(painted_parts) == 1 else "multi_paint"
    if re.search(r"رنگ\s*شدگی|رنگشدگی|رنگ\s*دارد|چند\s*رنگ|لکه\s*رنگ", positive):
        return "unknown"  # Extent unspecified; do not invent a damage count.
    if re.search(r"(?:بدون\s*(?:رنگ|تصادف)|بی\s*رنگ|بیرنگ)\s*نیست", text):
        return "unknown"
    if re.search(r"صافکاری|مشکوک|نیاز\s*به\s*کارشناسی|به\s*جز|بجز", positive):
        return "unknown"
    return "clean" if clean else "unknown"


def normalize_source_text(source: str, raw_text: str) -> SourceNormalization:
    text = normalize_text(raw_text)
    price, price_reason = parse_vehicle_price(text, allow_shorthand=(source.strip().casefold() == "telegram"))
    year, year_reason = parse_model_year(text)
    mileage, mileage_reason = parse_mileage(text)
    return SourceNormalization(
        source=source.strip().casefold(), raw_text=raw_text, normalized_text=text,
        price=price, model_year=year, body_condition=classify_body_condition(text),
        finance_blocked=contains_finance_phrase(text) and price is None, price_reason=price_reason,
        year_reason=year_reason, mileage=mileage, mileage_reason=mileage_reason,
    )


def run_self_test() -> None:
    def check(actual, expected, label):
        if actual != expected:
            raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    prices = {
        "قیمت 1,250,000,000 تومان": 1_250_000_000,
        "قیمت: 1250": 1_250_000_000,
        "قیمت 1/250": 1_250_000_000,
        "قیمت ۱.۲۵۰": 1_250_000_000,
        "قیمت\n۱۲۵۰": 1_250_000_000,
        "1.25 میلیارد تومان": 1_250_000_000,
        "قیمت 850 میلیون": 850_000_000,
        "قیمت ۱ میلیارد و ۲۵۰ میلیون تومان": 1_250_000_000,
        "قیمت 12,500,000,000 ریال": 1_250_000_000,
        "پژو 206 تیپ 5 مدل1397 قیمت 1/250": 1_250_000_000,
        "قیمت 1,250,000,000\nتماس 09123456789": 1_250_000_000,
        "قیمت 1250\nقیمت 1250": 1_250_000_000,
        "قیمت ۱/۲۵۰\nپیش پرداخت ۳۰۰ میلیون": 1_250_000_000,
        "قیمت 1250\nپرداخت اولیه 300 میلیون": 1_250_000_000,
        "قیمت 1250\nاقساطی": 1_250_000_000,
        "پیش پرداخت 300 میلیون": None,
        "ودیعه 500 میلیون": None,
        "قسط ماهانه 40 میلیون": None,
        "شماره تماس 09123456789": None,
        "قیمت 09123456789": None,
        "پژو 206 مدل 1397 کارکرد 120000": None,
        "قیمت 1250\nقیمت 1400": None,
        "قیمت یک میلیارد": None,
        "قیمت 1 میلیارد و 250": None,
        "قیمت 1400 تومان": None,
        "قیمت 1250 دلار": None,
        "قیمت 1250 هزار تومان": None,
        "قیمت 1250 تا 1400": None,
        "قیمت -1250": None,
    }
    for text, expected in prices.items():
        check(parse_vehicle_price(text)[0], expected, text)
    for text, expected in (("مدل 97", 1397), ("مدل۱۴۰۲", 1402), ("مدل 03", 1403),
                           ("Toyota 2017", 2017), ("مدل 2017", 2017),
                           ("قیمت 1400", None), ("کارکرد 2000", None),
                           ("پژو 2008", None), ("مدل1397 مدل1398", None)):
        check(parse_model_year(text)[0], expected, text)
    for text, expected in (("بدون رنگ شاسی سالم", "clean"), ("بدون تصادف و بدون رنگ", "clean"),
                           ("رنگشدگی ندارد", "clean"), ("بدون رنگ, تعویض روغن", "clean"),
                           ("یک لکه رنگ", "minor_paint"), ("دو لکه رنگ", "multi_paint"),
                           ("دور رنگ", "full_paint"), ("درب جلو تعویض", "replaced"),
                           ("شاسی ضربه دارد", "structural"), ("تصادفی", "accident"),
                           ("موتور فابریک", "unknown"), ("شاسی سالم", "unknown"),
                           ("بدون رنگ نیست", "unknown")):
        check(classify_body_condition(text), expected, text)
    check(parse_mileage("کارکرد 120 هزار کیلومتر")[0], 120000, "mileage thousands")
    check(parse_mileage("کارکرد: 102,000")[0], 102000, "mileage grouped")
    check(parse_mileage("در حد صفر کیلومتر")[0], None, "like new")
    # Web-card fallback: grouped full prices may appear without a price label or
    # currency word. Keep this narrow so phone/mileage/finance components do not
    # become vehicle prices.
    check(parse_vehicle_price("پژو 206 تیپ 2 مدل 96 1,050,000,000 بابل", allow_shorthand=False)[0],
          1_050_000_000, "unlabeled grouped web-card price")
    check(parse_vehicle_price("کارکرد 120,000 کیلومتر", allow_shorthand=False)[0], None,
          "grouped mileage is not a price")
    check(parse_vehicle_price("تماس 09123456789", allow_shorthand=False)[0], None,
          "phone is not a price")
    check(parse_vehicle_price("پیش پرداخت 300,000,000", allow_shorthand=False)[0], None,
          "prepayment is not a full price")
    good = normalize_source_text("telegram", "پژو 206 تیپ 5\nمدل1397\nکارکرد 120 هزار\nبدون رنگ\nقیمت 1/250")
    check((good.price, good.model_year, good.mileage, good.body_condition),
          (1_250_000_000, 1397, 120000, "clean"), "structured result")
    finance = normalize_source_text("telegram", "پژو 206 تیپ 5\nمدل1397\nکارکرد 120 هزار\nبدون رنگ\nقیمت 1250\nامکان خرید اقساطی")
    check((finance.price, finance.finance_blocked), (1_250_000_000, False), "financing option must not hide full price")
    component = normalize_source_text("telegram", "پژو 206 تیپ 5\nمدل1397\nکارکرد 120 هزار\nبدون رنگ\nپیش پرداخت 300 میلیون")
    check((component.price, component.finance_blocked), (None, True), "finance component must not become vehicle price")
    print("source_normalizers self-test: OK")


if __name__ == "__main__":
    run_self_test()
