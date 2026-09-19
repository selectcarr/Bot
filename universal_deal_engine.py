#!/usr/bin/env python3
"""Authoritative, source-independent System B deal engine. Python 3.11+.

Policy v3:
- exact base identity: brand + model + trim + model_year
- secondary reference priority: mileage, then unified defect score
- missing secondary evidence never becomes a fabricated zero and is not a hard reject
- 3..10 unique comparable references across all sources
- candidate/reposts and duplicate observations are excluded
- final reference price is the exact MEDIAN of the selected samples
- confirmed deal range is 1..15 percent below that median, inclusive

The unified defect score is supplied by Stage 3 upstream:
  painted panels + replacement panels + any chassis hit once.
This module does not infer paint/replacement/chassis facts from prose, fetch ads,
write history, or send Telegram messages.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re
import unicodedata

ENGINE_VERSION = "3.0.0"
MIN_SAMPLES = 3
MAX_SAMPLES = 10
MIN_DEAL_PERCENT = 1.0
MAX_DEAL_PERCENT = 15.0
MAX_MILEAGE = 2_000_000
MAX_DEFECT_SCORE = 100

_UNKNOWN = frozenset({"", "unknown", "none", "null", "n/a", "unspecified",
                      "نامشخص", "نا مشخص", "نامعلوم", "-", "--", "?"})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


@dataclass(frozen=True)
class DealVehicle:
    # Original positional contract is preserved.
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
    condition: str = ""
    dedup_key: str = ""
    # Stage 3 soft evidence. None means unknown/partial, never zero by default.
    defect_score: Optional[int] = None
    evidence_state: str = "unknown"


@dataclass(frozen=True)
class DealDecision:
    is_deal: bool
    reason: str
    candidate_price: int
    market_average: Optional[int]  # compatibility name; value is median reference
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
    return isinstance(value, int) and not isinstance(value, bool)


def _year_domain(year: int) -> str:
    if not _integer(year):
        return ""
    if 1300 <= year <= 1499:
        return "solar_hijri"
    if 1900 <= year <= 2099:
        return "gregorian"
    return ""


def _valid_optional_mileage(value: object) -> bool:
    return value is None or (_integer(value) and 0 <= value <= MAX_MILEAGE)


def _valid_optional_defect_score(value: object) -> bool:
    return value is None or (_integer(value) and 0 <= value <= MAX_DEFECT_SCORE)


def _canonical_url(value: str) -> str:
    """Local canonicalization; identity-bearing query parameters are preserved."""
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold()
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            netloc += f":{port}"
        query = sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                       if not k.casefold().startswith("utm_")
                       and k.casefold() not in {"fbclid", "gclid"})
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
    if not _valid_optional_mileage(vehicle.mileage):
        return "invalid_mileage"
    if not _valid_optional_defect_score(vehicle.defect_score):
        return "invalid_defect_score"
    if not isinstance(vehicle.body_condition, str):
        return "invalid_body_condition_type"
    if not isinstance(vehicle.condition, str):
        return "invalid_condition_type"
    if not isinstance(vehicle.evidence_state, str):
        return "invalid_evidence_state"
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
        all(_known(x) for x in (left.brand, left.model, left.trim,
                                right.brand, right.model, right.trim))
        and _clean(left.brand) == _clean(right.brand)
        and _clean(left.model) == _clean(right.model)
        and _clean(left.trim) == _clean(right.trim)
        and bool(_year_domain(left.model_year))
        and _year_domain(left.model_year) == _year_domain(right.model_year)
        and left.model_year == right.model_year
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


def _order_key(vehicle: DealVehicle) -> tuple[str, str, str]:
    return (_clean(vehicle.source), str(vehicle.source_id).strip(), _canonical_url(vehicle.url))


def _same_observation(left: DealVehicle, right: DealVehicle) -> bool:
    if _validation_reason(left) or _validation_reason(right):
        return False
    return (
        same_identity(left, right)
        and left.price == right.price
        and left.mileage == right.mileage
        and left.defect_score == right.defect_score
        and _clean(left.body_condition) == _clean(right.body_condition)
    )


def _unique_history(
    history: Iterable[DealVehicle], candidate: Optional[DealVehicle] = None,
) -> tuple[list[DealVehicle], Counter]:
    """Resolve duplicate components before identity/ranking/sample limits."""
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


def _secondary_rank(candidate: DealVehicle, sample: DealVehicle) -> tuple:
    """Stage 3 ranking: mileage first, then unified defect score, never price."""
    if candidate.mileage is not None and sample.mileage is not None:
        mileage_rank = (0, abs(candidate.mileage - sample.mileage))
    elif candidate.mileage is None and sample.mileage is not None:
        mileage_rank = (0, 0)
    else:
        mileage_rank = (1, 0)

    cscore, sscore = candidate.defect_score, sample.defect_score
    if cscore is None:
        defect_rank = (0, 0) if sscore is not None else (1, 0)
    elif sscore == cscore:
        defect_rank = (0, 0)
    elif sscore is None:
        defect_rank = (1, 0)
    else:
        defect_rank = (2, abs(cscore - sscore))
    return mileage_rank + defect_rank + _order_key(sample)


def is_comparable(candidate: DealVehicle, sample: DealVehicle) -> bool:
    return (
        _valid_vehicle(candidate) and _valid_vehicle(sample)
        and not (_tokens(candidate) & _tokens(sample))
        and same_identity(candidate, sample)
    )


def _select_samples(
    candidate: DealVehicle, history: Iterable[DealVehicle],
) -> tuple[list[DealVehicle], Counter]:
    unique, counts = _unique_history(history, candidate)
    eligible: list[DealVehicle] = []
    for sample in unique:
        if not same_identity(candidate, sample):
            counts["vehicle_identity_mismatch"] += 1
        else:
            eligible.append(sample)
    eligible.sort(key=lambda x: _secondary_rank(candidate, x))
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
    """Compatibility helper. Returns rounded median of one exact identity group."""
    unique, _ = _unique_history(samples)
    if len(unique) < MIN_SAMPLES:
        return None
    anchor = unique[0]
    same = [x for x in unique if same_identity(anchor, x)]
    if len(same) != len(unique):
        return None
    same.sort(key=_order_key)
    return int(round(_median_exact(same[:MAX_SAMPLES])))


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
    """Explicit adapter for NormalizedVehicleListing / stored-row adapters."""
    return DealVehicle(
        source=listing.source, source_id=listing.source_ad_id,
        brand=listing.brand, model=listing.model, trim=listing.trim,
        model_year=listing.model_year, mileage=listing.mileage,
        body_condition=listing.body_condition, price=listing.price,
        url=listing.url, condition=listing.condition, dedup_key=dedup_key,
        defect_score=getattr(listing, "defect_score", None),
        evidence_state=getattr(listing, "evidence_state", "unknown"),
    )


def run_self_test() -> None:
    """Offline behavior tests. Raises on failure even under python -O."""
    from dataclasses import replace
    import io
    import unittest

    class EngineTests(unittest.TestCase):
        def setUp(self):
            self.base = dict(brand="Peugeot", model="206", trim="تیپ 5",
                             model_year=1397, mileage=100_000, body_condition="clean",
                             defect_score=0, evidence_state="scored")
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
            flat = [replace(x, price=1_000_000_000) for x in self.h]
            for price, expected in [(990_000_000, True), (850_000_000, True),
                                    (990_000_001, False), (849_999_999, False)]:
                with self.subTest(price=price):
                    self.assertEqual(evaluate_deal(replace(self.c, price=price), flat).is_deal, expected)

        def test_three_not_two(self):
            self.assertEqual(evaluate_deal(self.c, self.h[:2]).reason, "insufficient_samples")

        def test_ten_cap(self):
            refs = [replace(self.h[0], source_id=f"r{i:02}", mileage=100_000 + i) for i in range(20)]
            d = evaluate_deal(self.c, refs)
            self.assertEqual(d.sample_count, 10)
            self.assertIn("over_sample_limit=10", d.details)

        def test_unknown_mileage_is_allowed(self):
            c = replace(self.c, mileage=None)
            h = [replace(x, mileage=None) for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)
            self.assertTrue(is_comparable(c, h[0]))

        def test_unknown_body_is_allowed(self):
            c = replace(self.c, body_condition="unknown")
            h = [replace(x, body_condition="unknown") for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)

        def test_structural_and_replaced_are_not_hard_rejects(self):
            c = replace(self.c, body_condition="structural", defect_score=1)
            h = [replace(x, body_condition="replaced", defect_score=1) for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)

        def test_exact_identity_is_hard_gate(self):
            h = [replace(x, model="207") for x in self.h]
            self.assertEqual(evaluate_deal(self.c, h).sample_count, 0)

        def test_mileage_priority_before_defect(self):
            refs = [
                replace(self.h[0], source_id="near_badscore", mileage=100_010, defect_score=5),
                replace(self.h[0], source_id="far_samescore", mileage=150_000, defect_score=0),
                replace(self.h[0], source_id="near2", mileage=100_020, defect_score=2),
                replace(self.h[0], source_id="near3", mileage=100_030, defect_score=3),
            ]
            selected = comparable_samples(self.c, refs)
            self.assertEqual([x.source_id for x in selected[:3]], ["near_badscore", "near2", "near3"])

        def test_defect_score_priority_after_mileage_tier(self):
            refs = [
                replace(self.h[0], source_id="same", url="https://example.com/same", mileage=100_000, defect_score=0),
                replace(self.h[0], source_id="missing_score", url="https://example.com/missing-score", mileage=100_000, defect_score=None),
                replace(self.h[0], source_id="different", url="https://example.com/different", mileage=100_000, defect_score=3),
            ]
            self.assertEqual([x.source_id for x in comparable_samples(self.c, refs)],
                             ["same", "missing_score", "different"])

        def test_candidate_with_unknown_secondary_evidence(self):
            c = replace(self.c, mileage=None, defect_score=None, body_condition="unknown")
            self.assertTrue(evaluate_deal(c, self.h).is_deal)

        def test_candidate_excluded(self):
            d = evaluate_deal(self.c, self.h[:2] + [self.c])
            self.assertEqual(d.sample_count, 2)
            self.assertIn("candidate_or_repost_excluded=1", d.details)

        def test_duplicate_records_not_three_samples(self):
            self.assertEqual(evaluate_deal(self.c, [self.h[0]] * 10).sample_count, 1)

        def test_shared_url_duplicate(self):
            a = replace(self.h[0], url="https://example.com/ad/1?utm_source=x#photo")
            b = replace(a, source="bama", source_id="copy", url="https://EXAMPLE.com/ad/1/")
            self.assertEqual(evaluate_deal(self.c, [a, b, self.h[2]]).sample_count, 2)

        def test_conflicting_duplicate_removed(self):
            a = self.h[0]
            b = replace(a, price=9_000_000_000)
            d = evaluate_deal(self.c, [a, b] + self.h[1:])
            self.assertEqual(d.sample_count, 2)
            self.assertIn("conflicting_duplicate_excluded=2", d.details)

        def test_same_specs_are_not_proof_of_duplicate(self):
            h = [replace(self.h[0], source_id=str(i)) for i in range(3)]
            self.assertEqual(evaluate_deal(self.c, h).sample_count, 3)

        def test_median_not_mean(self):
            h = [replace(x, price=p) for x, p in zip(self.h, [1_000_000_000, 1_000_000_000, 9_000_000_000])]
            self.assertEqual(evaluate_deal(self.c, h).market_average, 1_000_000_000)

        def test_even_median_exact_bounds(self):
            h = [replace(self.h[0], source_id=str(i), price=p) for i, p in enumerate(
                [99_999_999, 100_000_000, 100_000_001, 100_000_002])]
            d = evaluate_deal(replace(self.c, price=85_000_000), h)
            self.assertEqual(d.reason, "discount_above_maximum")
            self.assertTrue(evaluate_deal(replace(self.c, price=85_000_001), h).is_deal)

        def test_years_without_conversion(self):
            c = replace(self.c, model_year=2017)
            h = [replace(x, model_year=2017) for x in self.h]
            self.assertTrue(evaluate_deal(c, h).is_deal)
            self.assertFalse(same_identity(c, replace(c, model_year=1396)))

        def test_invalid_numeric_types(self):
            for field in ("price", "model_year"):
                for value in (True, False, "1000", float("nan"), -1):
                    self.assertFalse(evaluate_deal(replace(self.c, **{field: value}), self.h).is_deal)
            for value in (True, "1000", -1, 2_000_001):
                self.assertFalse(evaluate_deal(replace(self.c, mileage=value), self.h).is_deal)
            for value in (True, "1", -1, 101):
                self.assertFalse(evaluate_deal(replace(self.c, defect_score=value), self.h).is_deal)

        def test_explicit_adapter(self):
            from types import SimpleNamespace
            data = dict(self.c.__dict__)
            data["source_ad_id"] = data.pop("source_id")
            v = from_listing(SimpleNamespace(**data))
            self.assertEqual(v.defect_score, 0)
            self.assertTrue(evaluate_deal(v, self.h).is_deal)

    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EngineTests))
    if not result.wasSuccessful():
        raise AssertionError("Universal engine self-test failed:\n" + stream.getvalue())
    print("universal_deal_engine self-test: OK")
    print(f"universal_deal_engine version={ENGINE_VERSION} tests={result.testsRun} authority_policy=identity_soft_secondary_median")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.parse_args()
    run_self_test()
