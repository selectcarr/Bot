#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2: offline, source-independent interpretation of RECEIVED vehicle text.

Input: retained messages/search cards and identity-matched detail evidence from
stage 1. Output: versioned structured claims in a SEPARATE runtime database.
No network, no changes to market samples, no changes to sent memory. A complete
record is NOT permission to send a deal or an independent vehicle inspection.

Paint matching uses the NUMBER of repainted panels, never their locations.
Replacement and chassis claims remain independent. Unknown/conflicting evidence
is never defaulted to zero damage. This rule parser covers explicit patterns;
unrecognized prose needs review instead of fabricated certainty.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import source_normalizers as numeric

VERSION = "1.0.1"
SCHEMA_VERSION = 1
DATABASE_NAME = "normalized_vehicle_evidence.sqlite3"
OBSERVATIONS_NAME = "listing_observations.sqlite3"
DETAILS_NAME = "site_detail_evidence.sqlite3"
MAX_ROWS = 2000  # diagnostic evidence scope, NOT the market reference policy
MAX_TEXT = 30000
LOGGER = logging.getLogger("accurate_average.normalization")
SOURCES = {"telegram", "bale", "divar", "bama", "sheypoor", "karnameh", "formula", "khodro45", "hamrah_mechanic"}
NUMBERS = {"صفر": 0, "یک": 1, "یه": 1, "تک": 1, "دو": 2, "سه": 3, "چهار": 4,
           "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
           "یازده": 11, "دوازده": 12}
N = r"(?:دوازده|یازده|چهار|صفر|پنج|شش|هفت|هشت|یک|یه|تک|دو|سه|نه|ده|\d{1,2})"
PANEL = r"(?:درب\s*صندوق|در\s*صندوق|صندوق\s*عقب|گلگیر|کاپوت|درب|در|صندوق|سقف|ستون|رکاب|سینی|سپر)"
QUAL = r"(?:\s+(?:جلو|عقب|راست|چپ|سمت|راننده|شاگرد)){0,4}"
PART_RE = re.compile(r"(?<!\w)(?:(?P<n>" + N + r")\s+)?(?P<part>" + PANEL + r")(?P<q>" + QUAL + r")(?!\w)")
PAINT_RE = re.compile(r"(?<!\w)(?:رنگ\s*شدگی|رنگشدگی|رنگ\s*شده|رنگی|رنگ)(?!\w)")
REPLACED_RE = re.compile(r"(?<!\w)تعویض(?:\s*شده|ی)?(?!\w)")
UNCERTAIN = re.compile(r"شاید|احتمال|مشکوک|نامشخص|معلوم\s*نیست|نیاز\s*به\s*کارشناسی|کارشناسی\s*نشده|تایید\s*نشده|آیا|ایا")
EXCEPT = re.compile(r"به\s*جز|بجز|به\s*استثنا|جز\s+|الا\s+")
CLAUSE_SPLIT = re.compile(r"[\n,؛;.!]+|\s+(?:ولی|اما|لیکن)\s+")


def clean(value: str) -> str:
    value = numeric.normalize_text(html.unescape(str(value or ""))).casefold()
    value = re.sub(r"[\u064b-\u065f\u0670ـ]", "", value)
    value = re.sub(r"(?:شاسی\s*ها|شاسیها)", "شاسی", value)
    return value.strip()


def number(value: str) -> int | None:
    return int(value) if value.isdigit() and 0 <= int(value) <= 30 else NUMBERS.get(value)


def clauses(text: str) -> list[str]:
    return list(dict.fromkeys(s.strip() for s in CLAUSE_SPLIT.split(clean(text)) if s.strip()))


def _parts(text: str) -> tuple[dict[str, int], bool]:
    result: dict[str, int] = {}
    ambiguous = False
    for m in PART_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group("part"))
        name = {"درب": "در", "در صندوق": "صندوق", "درب صندوق": "صندوق", "صندوق عقب": "صندوق"}.get(name, name)
        qualifiers = tuple(sorted(set(m.group("q").split()) - {"سمت"}))
        # Only locations used to avoid counting a repeated mention twice. They
        # are deliberately excluded from the public comparison category.
        key = name + ("/" + "/".join(qualifiers) if qualifiers else "")
        if name == "در" and not qualifiers and not m.group("n"):
            tail = text[m.end():].strip()
            if tail and not re.match(r"و(?:\s|$)", tail):
                continue  # Persian preposition 'in', not an identified door
        count = number(m.group("n")) if m.group("n") else 1
        if count is None or count == 0 or (qualifiers and count > 1):
            ambiguous = True
            continue
        if key in result and result[key] != count:
            ambiguous = True
        result[key] = max(result.get(key, 0), count)
    return result, ambiguous


def _negated_action(clause: str, start: int, end: int) -> bool:
    before, after = clause[:start], clause[end:]
    if re.search(r"(?:بدون|فاقد|بی)\s*$", before):
        return True
    return bool(re.match(r"\s*(?:ندارد|ندارند|نیست|نیستند|نشده|نشده اند|نخورده|نداشته|ندیده)(?!\w)", after))


@dataclass(frozen=True)
class CountClaim:
    status: str  # clean / counted / full_paint / count_unknown / unknown / conflict
    count: int | None
    parts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    @property
    def category(self) -> str:
        if self.status == "clean":
            return "paint_count:0"
        if self.status == "counted" and self.count is not None:
            return f"paint_count:{self.count}"
        return self.status


def _count_claim(text: str, mode: str) -> CountClaim:
    action = PAINT_RE if mode == "paint" else REPLACED_RE
    counts: set[int] = set()
    named: dict[str, int] = {}
    quoted: list[str] = []
    issue: set[str] = set()
    global_zero = False
    full = False
    unspecified = False
    except_scope = False
    partial_clean = False
    for clause in clauses(text):
        # An oil/filter/battery/tyre service is NOT a replaced body panel.
        if mode == "replacement" and re.search(r"تعویض\s*(?:روغن|فیلتر|لاستیک|باتری|تسمه|لنت|دیسک|صفحه|شمع)", clause) and not PART_RE.search(clause):
            continue
        if mode == "paint" and re.fullmatch(r"(?:رنگ|رنگ بدنه)\s*[:=]?\s*(?:سفید|مشکی|نقره ای|خاکستری|قرمز|آبی|نوک مدادی|بژ|قهوه ای)", clause):
            continue
        if (UNCERTAIN.search(clause) or "؟" in clause or "?" in clause) and action.search(clause):
            issue.add("uncertain_" + mode + "_claim")
            unspecified = True
            continue
        if mode == "paint":
            # Standalone full-body clean claims. Clean individual panels do not
            # establish that the whole car has no repainted panels.
            zero = re.search(r"بدون\s*رنگ|بی\s*رنگ|بیرنگ|رنگ\s*شدگی\s*ندارد|رنگشدگی\s*ندارد|فاقد\s*رنگ\s*شدگی", clause)
            double_negative = bool(re.search(r"(?:بدون\s*رنگ|بی\s*رنگ|بیرنگ)\s*(?:نیست|نبوده)|(?:رنگشدگی|رنگ\s*شدگی)\s*ندارد\s*نیست", clause))
            if double_negative:
                unspecified = True
                quoted.append(clause)
                continue
            exception = EXCEPT.search(clause)
            if zero and exception:
                except_scope = True
                clause = clause[exception.end():]
                if not action.search(clause):
                    clause += " رنگ"
                zero = None
            if zero:
                if PART_RE.search(clause[:zero.start()]):
                    partial_clean = True
                elif re.search(r"بقیه|مابقی|سایر", clause[:zero.start()]):
                    except_scope = True
                else:
                    global_zero = True
                    quoted.append(clause)
            full_here = re.search(r"(?<!\w)(?:تمام|کامل|دور)\s*رنگ(?!\w)", clause)
            if full_here and not _negated_action(clause, full_here.start(), full_here.end()):
                if EXCEPT.search(clause) or UNCERTAIN.search(clause):
                    issue.add("qualified_or_uncertain_full_paint")
                    unspecified = True
                else:
                    full = True
                    quoted.append(clause)
        else:
            zero = re.search(r"بدون\s*تعویض(?:\s*قطعه)?|فاقد\s*(?:قطعه\s*)?تعویض|تعویضی\s*ندارد|تعویض\s*(?:قطعه\s*)?ندارد", clause)
            if zero and not PART_RE.search(clause[:zero.start()]):
                if re.search(r"(?:بدون\s*تعویض|تعویضی\s*ندارد)\s*نیست", clause):
                    unspecified = True
                else:
                    global_zero = True
                    quoted.append(clause)
        matches = [m for m in action.finditer(clause) if not _negated_action(clause, m.start(), m.end())]
        # The action 'رنگ' inside بیرنگ/بی رنگ is either not a word, or negated.
        if not matches:
            continue
        if UNCERTAIN.search(clause):
            issue.add("uncertain_" + mode + "_claim")
            unspecified = True
            continue
        total_re = (r"(?<!\w)(" + N + r")\s*(?:قطعه|تکه)\s*(?:از\s*بدنه\s*)?" +
                    (r"رنگ(?:\s*شدگی|شدگی|\s*شده|ی)?" if mode == "paint" else r"تعویض(?:ی|\s*شده)?"))
        for m in re.finditer(total_re, clause):
            if not _negated_action(clause, m.start(), m.end()):
                v = number(m.group(1))
                if v is not None:
                    counts.add(v)
                    quoted.append(clause)
        for m in re.finditer(r"(?:تعداد\s*)?(?:قطعات|تکه های|قطعه های)\s*" + (r"رنگ\s*شده" if mode == "paint" else r"تعویض\s*شده") + r"\s*[:=]\s*(" + N + r")(?!\w)", clause):
            v = number(m.group(1))
            if v is not None:
                counts.add(v)
                quoted.append(clause)
        # Each action receives only the adjacent explicit list of panels.
        previous = 0
        for m in matches:
            prefix_start = previous
            # Negative actions also form a boundary; do not carry their panels
            # into the next positive action ("fender not painted; hood painted").
            for pattern in (PAINT_RE, REPLACED_RE):
                for earlier in pattern.finditer(clause, 0, m.start()):
                    prefix_start = max(prefix_start, earlier.end())
            prefix = clause[prefix_start:m.start()]
            next_action = action.search(clause, m.end())
            suffix = clause[m.end():next_action.start() if next_action else len(clause)]
            found, ambiguous = _parts(prefix)
            if not found and re.fullmatch(r"\s*(?:شدگی|شده|ی)?\s*", prefix):
                found, ambiguous = _parts(suffix)
            if re.search(r"(?:جلو|عقب|چپ|راست)\s+و\s+(?:جلو|عقب|چپ|راست)", prefix):
                ambiguous = True  # elided panel noun: do not invent multiplicity
            if ambiguous:
                issue.add("ambiguous_panel_mentions")
            for key, count in found.items():
                if key in named and named[key] != count:
                    issue.add("conflicting_panel_mentions")
                named[key] = max(named.get(key, 0), count)
            if found:
                quoted.append(clause)
            previous = m.end()
        # An unspecified repaint/replacement cannot default to a number.
        generic = (r"چند\s*(?:قطعه|تکه|لکه)?\s*رنگ|لکه\s*رنگ|رنگ\s*(?:شدگی|دارد)|رنگشدگی" if mode == "paint"
                   else r"قطعه\s*تعویضی|تعویضی\s*دارد|چند\s*(?:قطعه|تکه)\s*تعویض")
        if re.search(generic, clause) and not counts and not named and not full:
            unspecified = True
            quoted.append(clause)
    # An unspecified panel overlapping a located panel must not count twice.
    roots = {key.split("/")[0] for key in named}
    for root in roots:
        if root in named and any(k.startswith(root + "/") for k in named):
            issue.add("overlapping_panel_mentions")
    named_count = sum(named.values())
    if len(counts) > 1:
        issue.add("conflicting_" + mode + "_counts")
    if counts and named_count > max(counts):
        issue.add("count_smaller_than_named_panels")
    has_damage = full or named_count > 0 or any(v > 0 for v in counts) or unspecified
    if global_zero and has_damage and not except_scope:
        issue.add("clean_and_damage_conflict")
    if full and (named_count or counts or partial_clean):
        issue.add("full_and_partial_conflict")
    if issue:
        return CountClaim("conflict" if any("conflict" in x or "smaller" in x for x in issue) else "count_unknown",
                          None, tuple(sorted(named)), tuple(dict.fromkeys(quoted))[:12], tuple(sorted(issue)))
    if full:
        return CountClaim("full_paint", None, (), tuple(dict.fromkeys(quoted))[:12])
    if counts:
        count = next(iter(counts))
        return CountClaim("clean" if count == 0 else "counted", count, tuple(sorted(named)), tuple(dict.fromkeys(quoted))[:12])
    if named_count:
        return CountClaim("counted", named_count, tuple(sorted(named)), tuple(dict.fromkeys(quoted))[:12])
    if global_zero and not unspecified:
        return CountClaim("clean", 0, (), tuple(dict.fromkeys(quoted))[:12])
    return CountClaim("count_unknown" if unspecified else "unknown", None, (), tuple(dict.fromkeys(quoted))[:12])


def parse_paint(text: str) -> CountClaim:
    return _count_claim(text, "paint")


def parse_replacements(text: str) -> CountClaim:
    return _count_claim(text, "replacement")


@dataclass(frozen=True)
class ChassisClaim:
    status: str  # healthy_claim, damaged_claim, partial, unknown, conflict
    front: str = "unknown"
    rear: str = "unknown"
    evidence: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


def parse_chassis(text: str) -> ChassisClaim:
    states: dict[str, set[str]] = {"front": set(), "rear": set()}
    evidence = []
    previous_chassis = False
    damage = r"(?:ضربه|آسیب|جوش|ترک|کجی|کج|تعویض)"
    listing = damage + r"(?:\s*و\s*" + damage + r")*"
    no_damage_patterns = [
        r"(?:بدون|فاقد)\s*(?:هیچ\s*گونه\s*)?" + listing,
        listing + r"\s*(?:ندارد|ندارند|نخورده|ندیده|نشده)(?!\w)",
    ]
    for original in clauses(text):
        clause = original
        if "شاسی" not in clause:
            if previous_chassis and re.match(r"(?:جلو|عقب)\b", clause):
                clause = "شاسی " + clause
            else:
                previous_chassis = False
                continue
        previous_chassis = True
        uncertain = bool(UNCERTAIN.search(clause) or "؟" in clause or "?" in clause)
        expanded = clause
        if not re.search(r"شاسی\s*(?:جلو\s*و\s*عقب|عقب\s*و\s*جلو)\s", clause):
            expanded = re.sub(r"\s+و\s+(?=(?:عقب|جلو)\s)", ";شاسی ", clause)
        expanded = re.sub(r"بدون\s*(ضربه|آسیب)\s*(?:به|در)\s*(شاسی(?:\s+(?:جلو|عقب))?)", r"\2 بدون \1", expanded)
        spans = re.split(r";|(?=شاسی)", expanded)
        for part in spans:
            if "شاسی" not in part:
                continue
            # An adjacent body panel or mechanical clause is not chassis evidence.
            part = re.split(r"\s+و\s+(?:کاپوت|گلگیر|درب|صندوق|سقف|ستون|سپر|موتور)\b", part)[0]
            targets = ["front"] if "جلو" in part and "عقب" not in part else ["rear"] if "عقب" in part and "جلو" not in part else ["front", "rear"]
            if uncertain:
                value = "uncertain"
            else:
                healthy = bool(re.search(r"(?<!\w)سالم(?!\s*(?:نیست|نبوده))|(?<!\w)فابریک(?!\s*نیست)", part))
                positive = part
                for pattern in no_damage_patterns:
                    for m in re.finditer(pattern, positive):
                        if re.search(r"ضربه|آسیب", m.group()):
                            healthy = True
                    positive = re.sub(pattern, " ", positive)
                damaged = bool(re.search(damage + r"|ناسالم|سالم\s*نیست", positive))
                value = ("conflict" if healthy and damaged else "healthy_claim" if healthy else
                         "damaged_claim" if damaged else "unknown")
            for target in targets:
                states[target].add(value)
            evidence.append(original)
    values = {}
    for target, claims in states.items():
        positive = claims - {"unknown", "uncertain"}
        values[target] = "conflict" if len(positive) > 1 or "conflict" in positive or ("uncertain" in claims and positive) else next(iter(positive), "unknown")
    if "conflict" in values.values():
        status = "conflict"
    elif "damaged_claim" in values.values():
        status = "damaged_claim"
    elif all(v == "healthy_claim" for v in values.values()):
        status = "healthy_claim"
    elif "healthy_claim" in values.values():
        status = "partial"
    else:
        status = "unknown"
    return ChassisClaim(status, values["front"], values["rear"], tuple(dict.fromkeys(evidence))[:12],
                        ("conflicting_chassis_claims",) if status == "conflict" else ())


def _safety_flags(text: str) -> tuple[str, ...]:
    text = clean(text)
    text = re.sub(r"بدون\s*(?:هیچ\s*گونه\s*)?تصادف|تصادف(?:ی)?\s*(?:نداشته|ندارد|نیست)|واژگون\s*نشده", " ", text)
    flags = []
    if re.search(r"تصادف|واژگون|چپی|آب\s*گرفتگی|سوختگی|آتش\s*سوزی", text):
        flags.append("accident_or_major_damage_claim")
    if re.search(r"(?:ستون|اتاق|سقف)" + QUAL + r"\s*(?:ضربه|جوش|تعویض|برش)|(?:جوش|تعویض|برش)\s*(?:ستون|اتاق|سقف)", text):
        flags.append("structural_repair_claim")
    return tuple(flags)


@dataclass(frozen=True)
class VehicleEvidence:
    source: str
    source_id: str
    url: str
    brand: str
    model: str
    trim: str
    model_year: int | None
    mileage: int | None
    price_toman: int | None
    usage: str
    paint: CountClaim
    chassis: ChassisClaim
    replacements: CountClaim
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    field_reasons: dict[str, str]
    input_sha256: str
    detail_used: bool
    version: str = VERSION

    @property
    def structured_fields_complete(self) -> bool:
        return not self.reasons

    @property
    def comparison_signature(self) -> str | None:
        """Draft source-independent key for the future bank/engine integration.

        This is not the legacy comparison_key and MUST NOT be written over it.
        Panel names are deliberately omitted. Chassis/replacement are retained.
        """
        if self.reasons:
            return None
        return json.dumps([clean(self.brand), clean(self.model), clean(self.trim), self.model_year,
                           self.usage, self.paint.category, self.chassis.status,
                           self.replacements.status, self.replacements.count], ensure_ascii=False,
                          separators=(",", ":"))


def _attribute_text(attributes: Iterable[tuple[str, str]]) -> tuple[list[str], dict[str, str], list[str]]:
    lines, identity, issues = [], {}, []
    attrs = [(clean(k), numeric.normalize_text(v)) for k, v in attributes if isinstance(k, str) and isinstance(v, str)]
    currencies = {v.casefold() for k, v in attrs if k in {"offer.pricecurrency", "pricecurrency"}}
    for k, v in attrs:
        if k in {"brand", "برند", "model", "مدل خودرو", "trim", "تیپ", "برند و تیپ"}:
            tag = {"brand": "brand", "برند": "brand", "model": "model", "مدل خودرو": "model", "trim": "trim", "تیپ": "trim", "برند و تیپ": "identity"}[k]
            if tag in identity and clean(identity[tag]) != clean(v):
                issues.append("conflicting_identity_attributes")
            identity[tag] = v
            continue
        if k in {"vehiclemodeldate", "model year", "سال", "سال ساخت", "سال تولید"}:
            lines.append("سال ساخت: " + v)
        elif k in {"کارکرد", "کارکرد خودرو", "mileage", "mileagefromodometer"}:
            if re.search(r"mile|smi|مایل", v, re.I):
                issues.append("unsupported_mileage_unit")
            else:
                v = re.sub(r"\s*(?:kmt|km|kilometers?|کیلومتر)\s*$", "", v, flags=re.I)
                lines.append("کارکرد: " + v + " کیلومتر")
        elif k in {"offer.price", "price"}:
            if len(currencies) == 1 and next(iter(currencies)) in {"irr", "irt", "toman", "تومان", "ریال"}:
                currency = next(iter(currencies))
                lines.append("قیمت کل: " + v + (" ریال" if currency in {"irr", "ریال"} else " تومان"))
            elif re.search(r"تومان|ریال|میلیون|میلیارد", v):
                lines.append("قیمت کل: " + v)
            else:
                issues.append("structured_price_currency_unknown")
        elif k in {"قیمت", "قیمت کل", "قیمت خودرو", "قیمت نقدی", "قیمت فروش"}:
            lines.append("قیمت کل: " + v)
        elif k in {"وضعیت بدنه", "body condition"}:
            lines.append("وضعیت بدنه: " + v)
        elif k in {"وضعیت شاسی", "شاسی", "شاسی جلو", "شاسی عقب", "وضعیت شاسی جلو", "وضعیت شاسی عقب"}:
            lines.append(k.replace("وضعیت ", "") + ": " + v)
    return lines, identity, issues


def _identity(title: str, raw_text: str, attributes: dict[str, str]) -> tuple[str, str, str, str]:
    # Reuse the installed catalogue, never infer the trim from a price.
    from accurate_average_collectors import extract_vehicle_identity, MODEL_ALIASES, normalize_for_match, _contains_alias
    primary = "\n".join([title, attributes.get("identity", ""), attributes.get("brand", ""), attributes.get("model", ""), attributes.get("trim", "")]).strip()
    if not primary:
        primary = "\n".join(raw_text.splitlines()[:2])
    brand, model, trim = extract_vehicle_identity(primary)
    # Detect distinct vehicle mentions; shorter aliases embedded in a longer
    # model name are not evidence of a second vehicle.
    normalized = normalize_for_match(primary)
    matching = [(normalize_for_match(alias), pair) for alias, pair in MODEL_ALIASES.items()
                if _contains_alias(normalized, normalize_for_match(alias))]
    longest = [(alias, pair) for alias, pair in matching if not any(alias != a and alias in a and len(a) > len(alias) for a, p in matching)]
    if len({p for a, p in longest}) > 1:
        return "", "", "", "ambiguous_vehicle_identity"
    if attributes.get("brand") and attributes.get("model") and not (brand and model):
        # Explicit primary vehicle attributes may preserve an unknown catalogue
        # model, but cross-source alias matching remains unverified.
        return attributes["brand"], attributes["model"], attributes.get("trim", ""), "identity_catalogue_unverified"
    explicit_trim = attributes.get("trim", "").strip()
    if explicit_trim:
        _, _, known_trim = extract_vehicle_identity(f"{brand or ''} {model or ''} {explicit_trim}")
        resolved = known_trim or explicit_trim
        if trim and clean(trim) != clean(resolved):
            return brand or "", model or "", "", "conflicting_trim"
        trim = resolved
    return brand or "", model or "", trim or "", "known_identity" if brand and model else "unknown_vehicle_identity"


def normalize_vehicle(*, source: str, source_id: str, url: str, title: str, raw_text: str,
                      detail_description: str = "", detail_title: str = "",
                      attributes: Iterable[tuple[str, str]] = (), truncated: bool = False,
                      warnings: Iterable[str] = ()) -> VehicleEvidence:
    if getattr(numeric, "API_VERSION", None) != 2:
        raise RuntimeError("vehicle_evidence_normalizer requires source_normalizers API_VERSION=2")
    source = str(source).strip().casefold()
    parts = list(dict.fromkeys(str(x).strip() for x in (title, raw_text, detail_title, detail_description) if str(x).strip()))
    attrs, explicit, attr_issues = _attribute_text(attributes)
    text = "\n".join(parts + attrs)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    truncated = truncated or len(text) > MAX_TEXT
    text = numeric.normalize_text(text[:MAX_TEXT])
    brand, model, trim, identity_reason = _identity(title or detail_title, raw_text or detail_description, explicit)
    # If two independently identified primary titles disagree, do not silently
    # replace the search identity with a different detail identity.
    if detail_title and title:
        b2, m2, t2, r2 = _identity(detail_title, "", explicit)
        if brand and b2 and (brand, model) != (b2, m2):
            identity_reason = "search_detail_identity_conflict"
    price, pr = numeric.parse_vehicle_price(text, allow_shorthand=source in {"telegram", "bale"})
    year, yr = numeric.parse_model_year(text)
    mileage, mr = numeric.parse_mileage(text)
    paint, chassis, replacements = parse_paint(text), parse_chassis(text), parse_replacements(text)

    # Project policy: silence about these condition axes means healthy.
    # Explicit uncertain, conflicting, painted, replaced or damaged claims are preserved.
    if paint.status == "unknown" and not paint.evidence:
        paint = CountClaim("clean", 0, (), (), ("assumed_clean_by_policy",))
    if replacements.status == "unknown" and not replacements.evidence:
        replacements = CountClaim("clean", 0, (), (), ("assumed_clean_by_policy",))
    if chassis.status == "unknown" and not chassis.evidence:
        chassis = ChassisClaim(
            "healthy_claim", "healthy_claim", "healthy_claim", (),
            ("assumed_healthy_by_policy",),
        )

    reasons = list(attr_issues)
    if source not in SOURCES or not str(source_id).strip():
        reasons.append("invalid_source_identity")
    if identity_reason != "known_identity":
        reasons.append(identity_reason)
    if not trim:
        reasons.append("trim_not_established")
    if year is None:
        reasons.append("year:" + yr)
    if price is None:
        reasons.append("price:" + pr)
    if mileage is None:
        reasons.append("mileage:" + mr)
    if paint.status not in {"clean", "counted", "full_paint"}:
        reasons.append("paint:" + paint.status)
    if chassis.status != "healthy_claim":
        reasons.append("chassis:" + chassis.status)
    if replacements.status not in {"clean", "counted"}:
        reasons.append("replacement:" + replacements.status)
    reasons.extend(_safety_flags(text))
    if truncated:
        reasons.append("input_truncated")
    if mileage == 0 and (paint.status != "clean" or replacements.status == "counted"):
        reasons.append("zero_vehicle_damage_conflict")
    return VehicleEvidence(source, str(source_id), str(url), brand, model, trim, year, mileage, price,
                           "unknown" if mileage is None else "zero" if mileage == 0 else "used",
                           paint, chassis, replacements, tuple(sorted(set(reasons))), tuple(sorted(set(warnings))),
                           {"price": pr, "year": yr, "mileage": mr, "identity": identity_reason},
                           digest, bool(detail_description or attrs))


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicle_normalizations (
 source_key TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
 version TEXT NOT NULL, observation_hash TEXT NOT NULL, detail_hash TEXT NOT NULL,
 normalized_at TEXT NOT NULL, structured_complete INTEGER NOT NULL,
 paint_category TEXT NOT NULL, chassis_status TEXT NOT NULL,
 replacement_status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS normalization_meta (
 key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def _read_rows(path: Path, sql: str, params: tuple = ()) -> list[dict]:
    if not path.is_file():
        return []
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def _detail_for(row: dict, detail: dict | None) -> tuple[str, str, list, bool, str, list[str]]:
    if detail is None:
        return "", "", [], False, "", []
    warnings = []
    from site_detail_reader import same_ad
    if (detail.get("source") != row["source"] or detail.get("source_id") != row["source_ad_id"]
            or detail.get("search_hash") != row["text_sha256"]
            or not same_ad(row["source"], str(row["url"]), str(detail.get("url", "")))):
        return "", "", [], False, "", ["unmatched_detail_ignored"]
    raw = detail.get("evidence_json", "")
    if not isinstance(raw, str) or len(raw) > 150000:
        return "", "", [], False, "", ["invalid_detail_ignored"]
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict) or not isinstance(obj.get("description", ""), str):
            raise ValueError("detail format")
        attrs = obj.get("attributes", [])
        if not isinstance(attrs, list) or any(not isinstance(x, (list, tuple)) or len(x) != 2 or not all(isinstance(y, str) for y in x) for x in attrs):
            raise ValueError("attributes format")
        if obj.get("status") not in {"captured", "description_not_found", "partial_truncated"} or obj.get("status") != detail.get("status") or not obj.get("identity_basis"):
            return "", "", [], False, "", ["unusable_detail_ignored"]
        truncated = bool(obj.get("description_truncated") or obj.get("fields_truncated") or obj.get("status") == "partial_truncated")
        return str(obj.get("title", "")), obj.get("description", ""), attrs, truncated, hashlib.sha256(raw.encode()).hexdigest(), warnings
    except (TypeError, ValueError):
        return "", "", [], False, "", ["invalid_detail_ignored"]


def normalize_retained(runtime: Path) -> dict:
    """Re-evaluate retained evidence only. Never open the market DB for writing.

    No TTL is imposed on market samples. Matching detail hash/link is required
    to reuse an observation; a changed card invalidates old detail evidence.
    The separate output cache has a count bound, not a price-sample retention rule.
    """
    runtime = Path(runtime)
    observations = _read_rows(runtime / OBSERVATIONS_NAME,
                              "SELECT * FROM listing_observations ORDER BY last_seen DESC, source_key LIMIT ?", (MAX_ROWS,))
    if not observations:
        empty = {"status": "no_received_observations", "version": VERSION, "sources": {}, "processed": 0,
                 "source_requests": 0, "market_writes": 0, "send_requests": 0}
        # Do not leave a previous extraction summary pretending to describe
        # evidence that is no longer available. This is NOT the market DB.
        if (runtime / DATABASE_NAME).is_file():
            conn = sqlite3.connect(runtime / DATABASE_NAME, timeout=10)
            try:
                conn.execute("DELETE FROM vehicle_normalizations")
                conn.execute("INSERT INTO normalization_meta VALUES('summary',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (json.dumps(empty),))
                conn.commit()
            finally:
                conn.close()
        LOGGER.info("[ACCURATE-SYSTEM] NormalizationStageSummary version=%s processed=0 status=no_received_observations source_requests=0 market_writes=0", VERSION)
        return empty
    details = {r["source_key"]: r for r in _read_rows(runtime / DETAILS_NAME, "SELECT * FROM detail_pages")}
    runtime.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(runtime / DATABASE_NAME, timeout=10)
    sources = {}
    report = {"status": "ok", "version": VERSION, "scope": "retained_received_evidence_not_this_run",
              "source_requests": 0, "market_writes": 0, "send_requests": 0,
              "processed": 0, "sources": sources}
    try:
        out.executescript(SCHEMA)
        for row in observations:
            source = row["source"]
            entry = sources.setdefault(source, {"processed": 0, "structured_complete": 0, "needs_review": 0,
                                                "detail_used": 0, "paint": Counter(), "chassis": Counter(),
                                                "replacements": Counter(), "reasons": Counter(), "warnings": Counter()})
            dt, desc, attrs, trunc, detail_hash, warnings = _detail_for(row, details.get(row["source_key"]))
            item = normalize_vehicle(source=source, source_id=row["source_ad_id"], url=row["url"],
                                     title=row["title"], raw_text=row["received_text"], detail_title=dt,
                                     detail_description=desc, attributes=attrs,
                                     truncated=bool(row["text_truncated"]) or trunc, warnings=warnings)
            payload = asdict(item)
            payload["comparison_signature"] = item.comparison_signature
            payload["permission_to_send"] = False
            # UPDATE replaces all extracted claims. Never keep obsolete clean
            # evidence when a later observation removes/contradicts the claim.
            out.execute("INSERT INTO vehicle_normalizations VALUES(?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(source_key) DO UPDATE SET source=excluded.source,source_id=excluded.source_id,"
                        "version=excluded.version,observation_hash=excluded.observation_hash,detail_hash=excluded.detail_hash,"
                        "normalized_at=excluded.normalized_at,structured_complete=excluded.structured_complete,"
                        "paint_category=excluded.paint_category,chassis_status=excluded.chassis_status,"
                        "replacement_status=excluded.replacement_status,payload_json=excluded.payload_json",
                        (row["source_key"], source, row["source_ad_id"], VERSION, row["text_sha256"], detail_hash,
                         datetime.now(timezone.utc).isoformat(), int(item.structured_fields_complete), item.paint.category,
                         item.chassis.status, item.replacements.status,
                         json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
            entry["processed"] += 1
            entry["structured_complete"] += int(item.structured_fields_complete)
            entry["needs_review"] += int(not item.structured_fields_complete)
            entry["detail_used"] += int(item.detail_used)
            entry["paint"][item.paint.category] += 1
            entry["chassis"][item.chassis.status] += 1
            entry["replacements"][item.replacements.status] += 1
            entry["reasons"].update(item.reasons)
            entry["warnings"].update(item.warnings)
            report["processed"] += 1
        # This is only the new normalization output cache, never aa_listings or
        # sent history. Reconcile it with the retained observation snapshot.
        keys = [r["source_key"] for r in observations]
        out.execute("CREATE TEMP TABLE keep_keys (k TEXT PRIMARY KEY)")
        out.executemany("INSERT INTO keep_keys VALUES(?)", ((k,) for k in keys))
        out.execute("DELETE FROM vehicle_normalizations WHERE source_key NOT IN (SELECT k FROM keep_keys)")
        out.execute("INSERT INTO normalization_meta VALUES('summary',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(report, sort_keys=True),))
        out.commit()
    except Exception:
        out.rollback()
        raise
    finally:
        out.close()
    for source, entry in sorted(sources.items()):
        LOGGER.info("[ACCURATE-SYSTEM] NormalizationEvidence source=%s processed=%s structured_complete=%s needs_review=%s "
                    "detail_used=%s paint=%s chassis=%s scope=retained_evidence",
                    source, entry["processed"], entry["structured_complete"], entry["needs_review"],
                    entry["detail_used"], json.dumps(entry["paint"], sort_keys=True), json.dumps(entry["chassis"], sort_keys=True))
    LOGGER.info("[ACCURATE-SYSTEM] NormalizationStageSummary version=%s processed=%s source_requests=0 market_writes=0 "
                "send_requests=0 pricing_unchanged=true", VERSION, report["processed"])
    return report


def read_summary(runtime: Path) -> dict:
    rows = _read_rows(Path(runtime) / DATABASE_NAME, "SELECT value FROM normalization_meta WHERE key='summary'")
    return json.loads(rows[0]["value"]) if rows else {"status": "not_recorded_yet", "version": VERSION}


def run_self_test() -> None:
    # More adversarial integration tests are provided in the offline report.
    cases = [
        ("بی رنگ", "clean", 0), ("رنگ بدنه: سفید", "unknown", None),
        ("یک قطعه رنگ", "counted", 1), ("دو قطعه رنگ", "counted", 2),
        ("سه تکه رنگ", "counted", 3), ("یک گلگیر رنگ", "counted", 1),
        ("یک کاپوت رنگ", "counted", 1), ("دو در رنگ", "counted", 2),
        ("یک در و یک گلگیر رنگ", "counted", 2),
        ("گلگیر جلو راست و در عقب چپ رنگ شده", "counted", 2),
        ("کاپوت رنگ شده\nکاپوت رنگ شده", "counted", 1),
        ("دو قطعه رنگ: در جلو چپ و گلگیر عقب راست رنگ شده", "counted", 2),
        ("چند تکه رنگ", "count_unknown", None), ("تمام رنگ", "full_paint", None),
        ("بدون رنگ نیست", "count_unknown", None),
        ("بی رنگ\nدو قطعه رنگ", "conflict", None),
        ("بدون رنگ به جز یک گلگیر", "counted", 1),
        ("شاسی سالم، تعویض روغن", "unknown", None),
        ("احتمالا یک گلگیر رنگ", "count_unknown", None),
        ("بی رنگ، چند تکه رنگ", "conflict", None),
        ("یک گلگیر رنگ ندارد و کاپوت رنگ شده", "counted", 1),
        ("درب و گلگیر رنگ ندارند", "unknown", None),
        ("تمام رنگ، سقف بی رنگ", "conflict", None),
        ("یک گلگیر رنگ و درب تعویض", "counted", 1),
        ("گلگیر جلو چپ و عقب راست رنگ شده", "count_unknown", None),
    ]
    count = 0
    for text, state, n in cases:
        got = parse_paint(text)
        if (got.status, got.count) != (state, n):
            raise AssertionError(f"paint case {count}: expected {(state,n)}, got {got}")
        count += 1
    for text, want in [("شاسی سالم", "healthy_claim"), ("شاسی جلو سالم", "partial"),
                       ("شاسی جلو سالم، شاسی عقب سالم", "healthy_claim"),
                       ("شاسی ضربه دارد", "damaged_claim"), ("شاسی ضربه ندارد", "healthy_claim"),
                       ("شاسی سالم نیست", "damaged_claim"), ("شاسی مشکوک", "unknown"),
                       ("بدون رنگ", "unknown"), ("شاسی سالم\nشاسی جلو ضربه دارد", "conflict"),
                       ("شاسی سالم؟", "unknown"), ("شاسی ناسالم", "damaged_claim"),
                       ("شاسی سالم، شاسی مشکوک", "conflict"),
                       ("شاسی بدون ضربه و جوش", "healthy_claim")]:
        got = parse_chassis(text)
        if got.status != want:
            raise AssertionError(f"chassis case {count}: {got} != {want}")
        count += 1
    for text, want, n in [("تعویض روغن", "unknown", None), ("بدون تعویض", "clean", 0),
                          ("درب جلو تعویضی", "counted", 1), ("دو قطعه تعویضی", "counted", 2),
                          ("یک گلگیر رنگ، بدون تعویض", "clean", 0),
                          ("بدون تعویض، قطعه تعویضی دارد", "conflict", None)]:
        got = parse_replacements(text)
        if (got.status, got.count) != (want, n):
            raise AssertionError(f"replacement case {count}: {got}")
        count += 1
    silent = normalize_vehicle(
        source="telegram", source_id="silent:1", url="https://t.me/test/2",
        title="پژو 206 تیپ 5",
        raw_text="پژو 206 تیپ 5\nمدل1397\nکارکرد120000\nقیمت 1250",
    )
    if not (
        silent.paint.status == "clean" and silent.paint.count == 0
        and silent.replacements.status == "clean" and silent.replacements.count == 0
        and silent.chassis.status == "healthy_claim"
        and "assumed_clean_by_policy" in silent.paint.issues
        and "assumed_healthy_by_policy" in silent.chassis.issues
    ):
        raise AssertionError("silent condition policy default failed")
    count += 1

    uncertain = normalize_vehicle(
        source="telegram", source_id="uncertain:1", url="https://t.me/test/3",
        title="پژو 206 تیپ 5",
        raw_text="پژو 206 تیپ 5\nمدل1397\nکارکرد120000\nقیمت 1250\nشاسی مشکوک\nچند تکه رنگ",
    )
    if uncertain.chassis.status == "healthy_claim" or uncertain.paint.status == "clean":
        raise AssertionError("explicit uncertain condition must not default to healthy")
    count += 1

    text = "پژو 206 تیپ 5\nمدل1397\nکارکرد120000\nقیمت 1250\nیک گلگیر رنگ\nشاسی سالم\nبدون تعویض"
    a = normalize_vehicle(source="telegram", source_id="test:1", url="https://t.me/test/1", title="پژو 206 تیپ 5", raw_text=text)
    b = normalize_vehicle(source="divar", source_id="test2", url="https://divar.ir/v/test/test2", title="پژو 206 تیپ 5", raw_text=text.replace("قیمت 1250", "قیمت 1250000000 تومان").replace("گلگیر", "کاپوت"))
    if not a.structured_fields_complete or a.comparison_signature != b.comparison_signature:
        raise AssertionError(f"source-independent panel count failed: {a.reasons}, {b.reasons}")
    count += 1
    print("vehicle evidence normalizer self-test: OK")
    print(f"vehicle evidence normalizer version={VERSION} tests={count} paint_rule=panel_count_not_location")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.parse_args()
    run_self_test()
