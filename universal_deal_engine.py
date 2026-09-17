#!/usr/bin/env python3
"""Offline, source-independent comparison engine. Python 3.11+; standard library.

Drop-in public API for the previously supplied universal_deal_engine.py.
Prices are INTEGER TOMAN, already validated as FULL vehicle prices upstream.
This module does NOT fetch advertisements, read descriptions, send messages,
change stored history, or automatically connect itself to any service.

Policy: 3..10 unique comparable references; 1..15 percent below the MEDIAN,
inclusive. 'market_average' is retained for compatibility, but is a median,
NOT an arithmetic mean. Decisions use the exact, unrounded rational median.

Missing mileage is NOT comparable. Explicit zero/used flags are respected;
when absent, only numerical mileage (zero / positive) determines this flag.
Body labels are claims supplied by the caller, not a physical inspection.
Structural/accident records require manual review, not automatic deals.
The broad legacy body label 'painted' requires clarification, not coercion.

Reference freshness and full-price validation remain upstream requirements.
Cross-source duplicates are excluded when they share a canonical URL or a
trusted dedup_key; similar specifications alone are NOT proof of duplication.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re
import unicodedata

ENGINE_VERSION = "2.0.0"
MIN_SAMPLES = 3
MAX_SAMPLES = 10
MIN_DEAL_PERCENT = 1.0
MAX_DEAL_PERCENT = 15.0
MAX_MILEAGE_DIFFERENCE = 60_000
MAX_MILEAGE = 2_000_000

# These are automatic-comparison labels, not expert assessments.
ALLOWED_BODY_CONDITIONS = frozenset({
    "clean", "minor_paint", "multi_paint", "full_paint", "replaced", "factory",
})
MANUAL_REVIEW_BODY_CONDITIONS = frozenset({"accident", "structural"})
_UNKNOWN = frozenset({"", "unknown", "none", "null", "n/a", "unspecified",
                      "نامشخص", "نا مشخص", "نامعلوم", "-", "--", "?"})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True)
class DealVehicle:
    # Original required fields and their order are preserved.
    source: str
    source_id: str
    brand: str
    model: str
    trim: str
    model_year: int
    mileage: Optional[int]
    body_condition: str
    price: int
    url: str = ""
    # Optional additions; old constructors remain valid.
    condition: str = ""  # "zero" or "used"; empty => numerical mileage only.
    dedup_key: str = ""  # Optional trusted SAME-VEHICLE key from upstream.


@dataclass(frozen=True)
class DealDecision:
    # Original required fields and their order are preserved.
    is_deal: bool
    reason: str
    candidate_price: int
    market_average: Optional[int]
    discount_percent: Optional[float]
    sample_count: int
    details: tuple[str, ...] = ()
    sample_keys: tuple[str, ...] = ()
    reference_method: str = "median"


def _clean(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value).translate(_DIGITS)
    value = value.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    value = re.sub(r"[\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]", "", value)
    return " ".join(value.casefold().split())


def _known(value: str) -> bool:
    return isinstance(value, str) and _clean(value) not in _UNKNOWN


def _integer(value: object) -> bool:
    # bool is a subclass of int, but is not a valid price/year/mileage.
    return isinstance(value, int) and not isinstance(value, bool)


def _year_domain(year: int) -> str:
    if not _integer(year):
        return ""
    if 1300 <= year <= 1499:
        return "solar_hijri"
    if 1900 <= year <= 2099:
        return "gregorian"
    return ""


def _condition(vehicle: DealVehicle) -> str:
    if not isinstance(vehicle.condition, str):
        return ""
    km = vehicle.mileage
    if not _integer(km) or not 0 <= km <= MAX_MILEAGE:
        return ""
    inferred = "zero" if km == 0 else "used"
    explicit = _clean(vehicle.condition)
    if not explicit:
        return inferred
    return explicit if explicit in {"zero", "used"} and explicit == inferred else ""


def _canonical_url(value: str) -> str:
    """Local URL normalization. Keep identity-bearing query parameters."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold()
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port  # May raise ValueError for malformed ports.
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            netloc += f":{port}"
        query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                       if not k.casefold().startswith("utm_")
                       and k.casefold() not in {"fbclid", "gclid"})
        # Preserve path case and percent encoding: these may be significant.
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((scheme, netloc, path, urlencode(query), ""))
    except (ValueError, TypeError):
        return ""


def _validation_reason(vehicle: object) -> str:
    if not isinstance(vehicle, DealVehicle):
        return "invalid_vehicle_type"
    if not _known(vehicle.source) or not _known(vehicle.source_id):
        return "missing_source_identity"
    if not _known(vehicle.brand) or not _known(vehicle.model) or not _known(vehicle.trim):
        return "incomplete_vehicle_identity"
    if not _year_domain(vehicle.model_year):
        return "invalid_model_year"
    if not _integer(vehicle.price) or vehicle.price <= 0:
        return "invalid_price"
    if vehicle.mileage is None:
        return "mileage_unknown"
    if not _integer(vehicle.mileage) or not 0 <= vehicle.mileage <= MAX_MILEAGE:
        return "invalid_mileage"
    if not isinstance(vehicle.condition, str) or not _condition(vehicle):
        return "condition_mileage_conflict"
    body = _clean(vehicle.body_condition)
    if body in MANUAL_REVIEW_BODY_CONDITIONS:
        return "body_requires_manual_review"
    if body not in ALLOWED_BODY_CONDITIONS:
        return "body_unknown_or_insufficient_detail"
    if body == "factory" and _condition(vehicle) != "zero":
        return "factory_body_on_used_vehicle"
    if not isinstance(vehicle.url, str) or (vehicle.url and not _canonical_url(vehicle.url)):
        return "invalid_url"
    if not isinstance(vehicle.dedup_key, str):
        return "invalid_dedup_key"
    return ""


def _valid_vehicle(vehicle: DealVehicle) -> bool:
    return not _validation_reason(vehicle)


def same_identity(left: DealVehicle, right: DealVehicle) -> bool:
    if not isinstance(left, DealVehicle) or not isinstance(right, DealVehicle):
        return False
    return (
        all(_known(x) for x in (left.brand, left.model, left.trim, right.brand, right.model, right.trim))
        and _clean(left.brand) == _clean(right.brand)
        and _clean(left.model) == _clean(right.model)
        and _clean(left.trim) == _clean(right.trim)
        and bool(_year_domain(left.model_year))
        and _year_domain(left.model_year) == _year_domain(right.model_year)
        and left.model_year == right.model_year
    )


def mileage_is_comparable(left: DealVehicle, right: DealVehicle) -> bool:
    if not isinstance(left, DealVehicle) or not isinstance(right, DealVehicle):
        return False
    return bool(_condition(left)) and _condition(left) == _condition(right) and (
        abs(left.mileage - right.mileage) <= MAX_MILEAGE_DIFFERENCE
    )


def _tokens(vehicle: DealVehicle) -> set[tuple[str, ...]]:
    tokens: set[tuple[str, ...]] = set()
    if _known(vehicle.source) and _known(vehicle.source_id):
        tokens.add(("source", _clean(vehicle.source), vehicle.source_id.strip()))
    url = _canonical_url(vehicle.url)
    if url:
        tokens.add(("url", url))
    if isinstance(vehicle.dedup_key, str) and vehicle.dedup_key.strip():
        tokens.add(("vehicle", vehicle.dedup_key.strip()))
    return tokens


def is_comparable(candidate: DealVehicle, sample: DealVehicle) -> bool:
    return (
        _valid_vehicle(candidate) and _valid_vehicle(sample)
        and not (_tokens(candidate) & _tokens(sample))
        and same_identity(candidate, sample)
        and _clean(candidate.body_condition) == _clean(sample.body_condition)
        and mileage_is_comparable(candidate, sample)
    )


def _order_key(vehicle: DealVehicle) -> tuple[str, str, str]:
    return (_clean(vehicle.source), str(vehicle.source_id).strip(), _canonical_url(vehicle.url))


def _same_observation(left: DealVehicle, right: DealVehicle) -> bool:
    """Duplicate observations must agree; no arbitrary highest-price selection."""
    if _validation_reason(left) or _validation_reason(right):
        return False
    return (
        same_identity(left, right) and left.price == right.price
        and left.mileage == right.mileage
        and _condition(left) == _condition(right)
        and _clean(left.body_condition) == _clean(right.body_condition)
    )


def _unique_history(
    history: Iterable[DealVehicle], candidate: Optional[DealVehicle] = None,
) -> tuple[list[DealVehicle], Counter]:
    """Resolve duplicate components BEFORE filtering and limiting references.

    Conflicting observations of the same identifiable ad are all excluded.
    Upstream should supply one fresh record per ad. Transitive duplicate links
    also prevent a repost of the candidate from leaking into its own median.
    """
    counts: Counter = Counter()
    records: list[DealVehicle] = [candidate] if candidate is not None else []
    for record in history:
        if not isinstance(record, DealVehicle):
            counts["invalid_vehicle_type"] += 1
        else:
            records.append(record)
    parents = list(range(len(records)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners: dict[tuple[str, ...], int] = {}
    for index, record in enumerate(records):
        for token in _tokens(record):
            if token in owners:
                parents[root(index)] = root(owners[token])
            else:
                owners[token] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(records)):
        groups.setdefault(root(index), []).append(index)

    unique: list[DealVehicle] = []
    for indices in groups.values():
        if candidate is not None and 0 in indices:
            counts["candidate_or_repost_excluded"] += len(indices) - 1
            continue
        observations = [records[i] for i in indices]
        if len(observations) > 1 and any(
            not _same_observation(observations[0], other) for other in observations[1:]
        ):
            counts["conflicting_duplicate_excluded"] += len(observations)
            continue
        chosen = min(observations, key=_order_key)
        counts["duplicate_excluded"] += len(observations) - 1
        reason = _validation_reason(chosen)
        if reason:
            counts[reason] += 1
        else:
            unique.append(chosen)
    return unique, counts


def _select_samples(
    candidate: DealVehicle, history: Iterable[DealVehicle],
) -> tuple[list[DealVehicle], Counter]:
    unique, counts = _unique_history(history, candidate)
    eligible: list[DealVehicle] = []
    for sample in unique:
        if not same_identity(candidate, sample):
            counts["vehicle_identity_mismatch"] += 1
        elif _clean(candidate.body_condition) != _clean(sample.body_condition):
            counts["body_condition_mismatch"] += 1
        elif not mileage_is_comparable(candidate, sample):
            counts["mileage_or_usage_mismatch"] += 1
        else:
            eligible.append(sample)
    # Price MUST NOT influence reference selection. Freshness is upstream.
    eligible.sort(key=lambda x: (abs(candidate.mileage - x.mileage), _order_key(x)))
    counts["over_sample_limit"] += max(0, len(eligible) - MAX_SAMPLES)
    return eligible[:MAX_SAMPLES], counts


def comparable_samples(candidate: DealVehicle, history: Iterable[DealVehicle]) -> list[DealVehicle]:
    if not _valid_vehicle(candidate):
        return []
    return _select_samples(candidate, history)[0]


def _median_exact(samples: list[DealVehicle]) -> Fraction:
    prices = sorted(sample.price for sample in samples)
    middle = len(prices) // 2
    if len(prices) % 2:
        return Fraction(prices[middle])
    return Fraction(prices[middle - 1] + prices[middle], 2)


def robust_market_average(samples: Iterable[DealVehicle]) -> Optional[int]:
    """Compatibility helper returning a ROUNDED MEDIAN, never an arithmetic mean.

    Supply candidate-comparable, fresh samples. This helper has no candidate
    with which to enforce a mileage window or exclude the candidate's own ad.
    Use evaluate_deal for the complete comparison, not this helper alone.
    """
    unique, _ = _unique_history(samples)
    unique.sort(key=_order_key)
    if len(unique) < MIN_SAMPLES:
        return None
    anchor = unique[0]
    if any(not same_identity(anchor, x)
           or _condition(anchor) != _condition(x)
           or _clean(anchor.body_condition) != _clean(x.body_condition)
           for x in unique[1:]):
        return None
    return int(round(_median_exact(unique[:MAX_SAMPLES])))


def discount_percent(candidate_price: int, market_average: int) -> Optional[float]:
    if not _integer(candidate_price) or not _integer(market_average):
        return None
    if candidate_price <= 0 or market_average <= 0:
        return None
    try:
        return float(Fraction(100 * (market_average - candidate_price), market_average))
    except OverflowError:
        return None


def evaluate_deal(candidate: DealVehicle, history: Iterable[DealVehicle]) -> DealDecision:
    problem = _validation_reason(candidate)
    if problem:
        price = getattr(candidate, "price", 0)
        price = price if _integer(price) else 0
        # Keep the original invalid_candidate reason for old consumers.
        return DealDecision(False, "invalid_candidate", price, None, None, 0, (problem,))

    samples, counts = _select_samples(candidate, history)
    details = tuple(f"{name}={count}" for name, count in sorted(counts.items()) if count)
    keys = tuple(f"{_clean(x.source)}|{x.source_id.strip()}" for x in samples)
    if len(samples) < MIN_SAMPLES:
        return DealDecision(False, "insufficient_samples", candidate.price,
                            None, None, len(samples), details, keys)

    reference = _median_exact(samples)
    exact_discount = 100 * (reference - candidate.price) / reference
    try:
        shown_discount = float(exact_discount)
    except OverflowError:
        return DealDecision(False, "invalid_discount", candidate.price,
                            int(round(reference)), None, len(samples), details, keys)

    if exact_discount < Fraction(str(MIN_DEAL_PERCENT)):
        reason = "discount_below_minimum"
    elif exact_discount > Fraction(str(MAX_DEAL_PERCENT)):
        reason = "discount_above_maximum"
    else:
        reason = "confirmed_deal"
    return DealDecision(reason == "confirmed_deal", reason, candidate.price,
                        int(round(reference)), shown_discount, len(samples), details, keys)


def from_listing(listing: object, *, dedup_key: str = "") -> DealVehicle:
    """Explicit adapter for the supplied NormalizedVehicleListing dataclass.

    No parsing, inference of missing body fields, I/O, or service registration.
    Missing required attributes raise an error rather than hiding integration bugs.
    """
    return DealVehicle(
        source=listing.source, source_id=listing.source_ad_id,
        brand=listing.brand, model=listing.model, trim=listing.trim,
        model_year=listing.model_year, mileage=listing.mileage,
        body_condition=listing.body_condition, price=listing.price,
        url=listing.url, condition=listing.condition, dedup_key=dedup_key,
    )


def run_self_test() -> None:
    """Offline behavior tests. Raises on failure even under python -O."""
    from dataclasses import replace
    import io
    import unittest

    class EngineTests(unittest.TestCase):
        def setUp(self):
            self.base = dict(brand="Peugeot", model="206", trim="تیپ 5",
                             model_year=1397, mileage=100_000, body_condition="clean")
            self.c = DealVehicle("telegram", "candidate", price=950_000_000, **self.base)
            self.h = [DealVehicle("telegram", "1", price=1_000_000_000, **self.base),
                      DealVehicle("bama", "2", price=1_020_000_000, **self.base),
                      DealVehicle("divar", "3", price=980_000_000, **self.base)]

        def test_original_example(self):
            d = evaluate_deal(self.c, self.h)
            self.assertTrue(d.is_deal)
            self.assertEqual((d.market_average, d.discount_percent, d.sample_count),
                             (1_000_000_000, 5.0, 3))
            self.assertEqual(d.reference_method, "median")

        def test_inclusive_boundaries(self):
            for price, expected in [(990_000_000, True), (850_000_000, True),
                                    (990_000_001, False), (849_999_999, False),
                                    (995_000_000, False), (840_000_000, False),
                                    (1_000_000_000, False), (1_100_000_000, False)]:
                with self.subTest(price=price):
                    self.assertEqual(evaluate_deal(replace(self.c, price=price), self.h).is_deal, expected)

        def test_three_not_two(self):
            self.assertEqual(evaluate_deal(self.c, self.h[:2]).reason, "insufficient_samples")
            self.assertFalse(evaluate_deal(self.c, []).is_deal)

        def test_ten_cap(self):
            refs = [replace(self.h[0], source_id=f"r{i:02}", mileage=100_000 + i) for i in range(20)]
            d = evaluate_deal(self.c, refs)
            self.assertEqual(d.sample_count, 10)
            self.assertIn("over_sample_limit=10", d.details)

        def test_reference_selection_not_price(self):
            refs = [replace(self.h[0], source_id=f"r{i:02}", mileage=100_000 + i) for i in range(20)]
            a = comparable_samples(self.c, refs)
            b = comparable_samples(self.c, [replace(x, price=x.price + i * 1_000_000) for i, x in enumerate(refs)])
            self.assertEqual([x.source_id for x in a], [x.source_id for x in b])
            self.assertEqual(a, comparable_samples(self.c, reversed(refs)))

        def test_mileage_required(self):
            c = replace(self.c, mileage=None)
            self.assertFalse(evaluate_deal(c, self.h).is_deal)
            self.assertFalse(mileage_is_comparable(c, self.h[0]))
            self.assertFalse(evaluate_deal(self.c, [replace(x, mileage=None) for x in self.h]).is_deal)

        def test_invalid_numeric_types(self):
            for field in ("price", "mileage", "model_year"):
                for value in (True, False, "1000", float("nan"), float("inf"), -1):
                    with self.subTest(field=field, value=value):
                        self.assertFalse(evaluate_deal(replace(self.c, **{field: value}), self.h).is_deal)
            self.assertFalse(evaluate_deal(replace(self.c, price=0), self.h).is_deal)

        def test_empty_identity(self):
            for field in ("source", "source_id", "brand", "model", "trim"):
                for value in ("", "unknown", "نامشخص", None):
                    with self.subTest(field=field, value=value):
                        self.assertFalse(evaluate_deal(replace(self.c, **{field: value}), self.h).is_deal)

        def test_years_without_conversion(self):
            c = replace(self.c, model_year=2017)
            h = [replace(x, model_year=2017) for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)
            self.assertFalse(same_identity(c, replace(c, model_year=1396)))
            self.assertFalse(evaluate_deal(replace(c, model_year=17), h).is_deal)

        def test_zero_not_used(self):
            self.assertFalse(is_comparable(replace(self.c, mileage=0), replace(self.h[0], mileage=1)))
            c = replace(self.c, mileage=0, condition="zero", body_condition="factory")
            h = [replace(x, mileage=0, condition="zero", body_condition="factory") for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)
            self.assertFalse(_valid_vehicle(replace(self.c, condition="zero")))
            self.assertFalse(_valid_vehicle(replace(self.c, mileage=0, condition="used")))
            self.assertFalse(_valid_vehicle(replace(self.c, body_condition="factory")))

        def test_mileage_window(self):
            self.assertTrue(is_comparable(self.c, replace(self.h[0], mileage=160_000)))
            self.assertFalse(is_comparable(self.c, replace(self.h[0], mileage=160_001)))
            self.assertFalse(_valid_vehicle(replace(self.c, mileage=2_000_001)))

        def test_unknown_and_damage(self):
            for body in ("unknown", "painted", "", "structural", "accident", "clean|painted"):
                with self.subTest(body=body):
                    self.assertFalse(evaluate_deal(replace(self.c, body_condition=body), self.h).is_deal)
            for body in ("minor_paint", "multi_paint", "full_paint", "replaced"):
                with self.subTest(body=body):
                    c = replace(self.c, body_condition=body)
                    self.assertFalse(evaluate_deal(c, self.h).is_deal)
                    self.assertTrue(evaluate_deal(c, [replace(x, body_condition=body) for x in self.h]).is_deal)

        def test_candidate_excluded(self):
            d = evaluate_deal(self.c, self.h[:2] + [self.c])
            self.assertFalse(d.is_deal)
            self.assertEqual(d.sample_count, 2)
            self.assertIn("candidate_or_repost_excluded=1", d.details)

        def test_duplicate_records_not_three_samples(self):
            self.assertEqual(evaluate_deal(self.c, [self.h[0]] * 10).sample_count, 1)
            self.assertIsNone(robust_market_average([self.h[0]] * 10))

        def test_shared_url_duplicate(self):
            a = replace(self.h[0], url="https://example.com/ad/1?utm_source=x#photo")
            b = replace(a, source="bama", source_id="copy", url="https://EXAMPLE.com/ad/1/")
            self.assertEqual(evaluate_deal(self.c, [a, b, self.h[2]]).sample_count, 2)

        def test_query_identity_preserved(self):
            a = replace(self.h[0], url="https://example.com/ad?id=1")
            b = replace(a, source_id="different", url="https://example.com/ad?id=2")
            self.assertEqual(evaluate_deal(self.c, [a, b, self.h[2]]).sample_count, 3)

        def test_conflicting_duplicate_removed(self):
            a = self.h[0]
            b = replace(a, price=9_000_000_000)
            d = evaluate_deal(self.c, [a, b] + self.h[1:])
            self.assertEqual(d.sample_count, 2)
            self.assertIn("conflicting_duplicate_excluded=2", d.details)

        def test_trusted_key(self):
            a = replace(self.h[0], dedup_key="same-car")
            b = replace(a, source="other", source_id="other")
            self.assertEqual(evaluate_deal(self.c, [a, b] + self.h[1:]).sample_count, 3)
            c = replace(self.c, dedup_key="same-car")
            self.assertEqual(evaluate_deal(c, [a, b] + self.h[1:]).sample_count, 2)

        def test_transitive_repost(self):
            c = replace(self.c, dedup_key="car-A")
            a = replace(self.h[0], dedup_key="car-A", url="https://example.com/a")
            b = replace(a, source_id="new", dedup_key="", source="bama")
            self.assertEqual(evaluate_deal(c, [a, b] + self.h[1:]).sample_count, 2)

        def test_same_specs_are_not_proof_of_duplicate(self):
            h = [replace(self.h[0], source_id=str(i)) for i in range(3)]
            self.assertEqual(evaluate_deal(self.c, h).sample_count, 3)

        def test_unicode_identity(self):
            c = replace(self.c, model="۲۰۶", trim="  تيپ\u200c۵ ", source=" TELEGRAM ")
            self.assertTrue(evaluate_deal(c, self.h).is_deal)

        def test_generator_and_no_mutation(self):
            before = list(self.h)
            self.assertEqual(evaluate_deal(self.c, iter(self.h)), evaluate_deal(self.c, self.h))
            self.assertEqual(self.h, before)

        def test_median_not_mean(self):
            h = [replace(x, price=p) for x, p in zip(self.h, [1_000_000_000, 1_000_000_000, 9_000_000_000])]
            self.assertEqual(evaluate_deal(self.c, h).market_average, 1_000_000_000)

        def test_even_median_and_exact_bounds(self):
            h = [replace(self.h[0], source_id=str(i), price=p) for i, p in enumerate(
                [99_999_999, 100_000_000, 100_000_001, 100_000_002])]
            # Median = 100000000.5. Rounded-median arithmetic would admit
            # 85,000,000 as exactly 15%; exact arithmetic correctly rejects it.
            d = evaluate_deal(replace(self.c, price=85_000_000), h)
            self.assertEqual(d.reason, "discount_above_maximum")
            self.assertTrue(evaluate_deal(replace(self.c, price=85_000_001), h).is_deal)

        def test_invalid_helper_inputs(self):
            self.assertIsNone(discount_percent(True, 100))
            self.assertIsNone(discount_percent(100, 0))
            self.assertIsNone(discount_percent(float("nan"), 100))
            self.assertIsNone(robust_market_average(self.h[:2]))
            self.assertIsNone(robust_market_average(self.h + [replace(self.h[0], source_id="bad", model="207")]))
            self.assertFalse(evaluate_deal(None, []).is_deal)
            self.assertFalse(is_comparable(self.c, None))
            self.assertFalse(evaluate_deal(self.c, [None, {}, self.h[0]]).is_deal)

        def test_url_validation(self):
            for value in ("not a url", "javascript:alert(1)", "https://x:bad/1", None):
                with self.subTest(url=value):
                    self.assertFalse(_valid_vehicle(replace(self.c, url=value)))

        def test_old_constructor_contract(self):
            v = DealVehicle("telegram", "x", "Peugeot", "206", "تیپ 5", 1397, 100000, "clean", 950000000, "")
            self.assertTrue(evaluate_deal(v, self.h).is_deal)
            d = DealDecision(False, "test", 1, None, None, 0)
            self.assertEqual(d.reference_method, "median")

        def test_explicit_adapter(self):
            from types import SimpleNamespace
            data = dict(self.c.__dict__)
            data["source_ad_id"] = data.pop("source_id")
            data["condition"] = "used"
            v = from_listing(SimpleNamespace(**data))
            self.assertEqual(v.condition, "used")
            self.assertTrue(evaluate_deal(v, self.h).is_deal)
            with self.assertRaises(AttributeError):
                from_listing(object())

    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(EngineTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise AssertionError("Universal engine self-test failed:\n" + stream.getvalue())
    print("universal_deal_engine self-test: OK")
    print(f"universal_deal_engine version={ENGINE_VERSION} tests={result.testsRun}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run offline tests (also the default).")
    parser.parse_args()
    run_self_test()
