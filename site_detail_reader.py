#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 1: bounded public listing-detail retrieval, evidence only.

No browser impersonation, login, contact lookup, image fetching, CAPTCHA solving,
proxy rotation, JavaScript execution, or automatic retries. This module never
normalizes a price, modifies market samples, or authorizes a Telegram message.
It writes only site_detail_evidence.sqlite3 inside the existing encrypted-runtime
scope. Local SQLite is not itself encrypted; the existing workflow encrypts it.

Selectors are conservative fallbacks, NOT a claim of verified live-site support.
No description found -> explicit missing status, never a clean-car inference.
All non-network tests run with --self-test (also the default CLI operation).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import re
import sqlite3
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

VERSION = "1.0.0"
LOGGER = logging.getLogger("accurate_average.details")
AGENT = "AccurateAverageSystemB"
USER_AGENT = "AccurateAverageSystemB/1.0 (public listing evidence; low-rate client)"
DATABASE_NAME = "site_detail_evidence.sqlite3"
HOSTS = {
    "divar": {"divar.ir", "www.divar.ir"},
    "bama": {"bama.ir", "www.bama.ir"},
    "khodro45": {"khodro45.com", "www.khodro45.com"},
    "formula": {"formula.ir", "www.formula.ir"},
    "sheypoor": {"sheypoor.com", "www.sheypoor.com"},
    "karnameh": {"karnameh.com", "www.karnameh.com"},
    "hamrah_mechanic": {"hamrah-mechanic.com", "www.hamrah-mechanic.com"},
}
PATHS = {
    "divar": r"/v/[^/]+/[^/]+/?",
    "bama": r"/car/detail-[^/]+/?",
    "khodro45": r"/(?:used-car|cars?)/[^/]+/[^/]+/?",
    "formula": r"/car/detail-[^/]+/?",
    "sheypoor": r"/v/[^/]+\.html/?",
    "karnameh": r"/used-cars/[0-9a-fA-F-]{36}/?",
    "hamrah_mechanic": r"/cars-for-sale/[^/]+/[^/]+/[0-9]+/?",
}
# Only dedicated description elements, not the text of the whole page.
SELECTORS = {
    "divar": (".kt-description-row__text", "[data-testid='post-description']"),
    "bama": (".bama-ad-detail-description", "[data-testid='ad-description']"),
    "khodro45": ("[data-testid='vehicle-description']",),
    "formula": ("[data-testid='car-description']",),
    "sheypoor": ("[data-testid='ad-description']",),
    "karnameh": ("[data-testid='car-description']",),
    "hamrah_mechanic": ("[data-testid='car-description']",),
}
BAD_ANCESTOR = re.compile(r"(?:recommend|related|similar|suggest|carousel|advert-banner)", re.I)
LABELS = {
    "برند", "مدل", "برند و تیپ", "تیپ", "سال", "سال ساخت", "مدل خودرو", "کارکرد",
    "کارکرد خودرو", "رنگ", "رنگ بدنه", "وضعیت بدنه", "وضعیت شاسی", "شاسی جلو", "شاسی عقب",
    "وضعیت شاسی جلو", "وضعیت شاسی عقب", "وضعیت موتور", "گیربکس", "نوع سوخت", "قیمت",
    "قیمت خودرو", "قیمت کل", "قیمت نقدی", "body condition", "mileage", "model year", "price",
}
HEADINGS = {"توضیحات", "توضیحات آگهی", "توضیحات خودرو", "توضیحات فروشنده", "description", "seller description"}
CHALLENGES = (
    "تایید کنید ربات نیستید", "تأیید کنید ربات نیستید", "تعداد درخواست های شما بیش از حد",
    "دسترسی شما محدود شده", "فعالیت غیرعادی", "verify you are human", "are you a robot",
    "too many requests", "access denied", "checking your browser",
)
FINANCE = ("اقساط", "پیش پرداخت", "پیشپرداخت", "ودیعه", "حواله", "ثبت نام", "لیزینگ", "پیش فروش")


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    text = text.replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    text = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return 0.0
        return dt.timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def valid_url(source: str, url: str) -> str:
    """Exact host allowlist; listing path only. Do not follow a URL from page text."""
    try:
        if not isinstance(url, str) or len(url) > 4096 or re.search(r"[\x00-\x20\\]", url):
            return ""
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS.get(source, set()):
            return ""
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            return ""
        path = unquote(parsed.path)
        if "%" in path or "\\" in path or re.search(r"[\x00-\x1f]", path):
            return ""
        if any(piece in {".", ".."} for piece in path.split("/")):
            return ""
        if not re.fullmatch(PATHS[source], path, re.I):
            return ""
        return urlunsplit(("https", parsed.hostname, parsed.path.rstrip("/"), "", ""))
    except (ValueError, KeyError):
        return ""


def same_ad(source: str, a: str, b: str) -> bool:
    a, b = valid_url(source, a), valid_url(source, b)
    if not a or not b:
        return False
    pa, pb = urlsplit(a), urlsplit(b)
    if pa.hostname.removeprefix("www.") != pb.hostname.removeprefix("www."):
        return False
    # Divar/Sheypoor IDs are the last path component; all other paths are exact.
    if source == "divar":
        return pa.path.split("/")[-1] == pb.path.split("/")[-1]
    return unquote(pa.path) == unquote(pb.path)


@dataclass(frozen=True)
class Candidate:
    source: str
    source_id: str
    url: str
    title: str
    search_hash: str
    observed_at: str
    outcome: str

    @property
    def key(self) -> str:
        return f"{self.source}|{self.source_id}"

    @property
    def public_ref(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Evidence:
    status: str
    title: str = ""
    description: str = ""
    attributes: tuple[tuple[str, str], ...] = ()
    extraction: str = "none"
    identity_basis: str = ""
    description_truncated: bool = False
    fields_truncated: bool = False
    reason: str = ""


@dataclass(frozen=True)
class Policy:
    max_pages: int = 2
    max_http: int = 4  # includes robots.txt, failed attempts and every detail GET
    max_pages_per_source: int = 1
    max_pages_per_source_day: int = 2
    max_http_per_source_day: int = 6
    delay_min: float = 30.0
    delay_max: float = 60.0
    max_seconds: float = 360.0
    minimum_seconds_for_request: float = 45.0
    refresh_hours: int = 24  # network-response cache, NOT market sample expiry
    cooldown_hours: int = 24
    max_bytes: int = 2_000_000
    max_description_chars: int = 20_000
    enabled: bool = True

    @classmethod
    def from_environment(cls, cooldown_hours: int = 24) -> Policy:
        # Bounded opt-down controls; no env variable can increase hard request caps.
        raw = os.getenv("ACCURATE_DETAIL_MAX_PAGES_PER_RUN", "2")
        try:
            pages = max(0, min(2, int(raw)))
        except ValueError as exc:
            raise ValueError("ACCURATE_DETAIL_MAX_PAGES_PER_RUN must be an integer") from exc
        enabled = os.getenv("ACCURATE_DETAIL_ENABLED", "true").strip().lower()
        if enabled not in {"true", "false", "1", "0"}:
            raise ValueError("ACCURATE_DETAIL_ENABLED must be true or false")
        return cls(max_pages=pages, cooldown_hours=max(24, int(cooldown_hours)),
                   enabled=enabled in {"true", "1"} and pages > 0)


@dataclass(frozen=True)
class HttpPage:
    status: int
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str = ""


class PublicTransport:
    """One bounded GET, zero automatic redirects/retries, no environment auth."""
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fa,en;q=0.5",
                                     "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8"})
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def get(self, url: str, max_bytes: int) -> HttpPage:
        start = time.monotonic()
        try:
            with self.session.get(url, timeout=(8, 20), allow_redirects=False,
                                  stream=True, verify=True) as response:
                headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                if response.status_code != 200:
                    return HttpPage(response.status_code, headers=headers)
                data = bytearray()
                for chunk in response.iter_content(chunk_size=16_384):
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        return HttpPage(200, headers=headers, error="response_too_large")
                    if time.monotonic() - start > 35:
                        return HttpPage(200, headers=headers, error="response_deadline")
                # These public Persian sites normally use UTF-8; preserve explicit
                # charsets, never infer facts from decoding replacement characters.
                charset = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I)
                encoding = charset.group(1) if charset else "utf-8-sig"
                try:
                    body = data.decode(encoding)
                except (LookupError, UnicodeDecodeError):
                    return HttpPage(200, headers=headers, error="unsupported_encoding")
                return HttpPage(200, body, headers)
        except requests.RequestException as exc:
            return HttpPage(0, error=type(exc).__name__)

    def close(self):
        self.session.close()


def challenge(body: str) -> bool:
    soup = BeautifulSoup(body, "html.parser")
    for node in soup.select("script, style, noscript, svg, template"):
        node.decompose()
    visible = normalized(soup.get_text(" ", strip=True))
    return any(normalized(x) in visible for x in CHALLENGES) or bool(
        soup.select("form#challenge-form, #cf-challenge-running, [data-testid='captcha-challenge']"))



def _robot_path(value: str) -> str:
    # RFC 9309: compare percent-encoded non-ASCII/reserved bytes without decoding
    # encoded '/' into a path separator. Decode only percent-encoded unreserved.
    def pct(match):
        c = chr(int(match.group(1), 16))
        return c if c.isascii() and (c.isalnum() or c in "-._~") else "%" + match.group(1).upper()
    return re.sub(r"%([0-9a-fA-F]{2})", pct, quote(value, safe="/%*?$&=:@!+,;~-._"))


def robots_permission(body: str, url: str) -> bool:
    """Conservative REP evaluator with wildcard/longest-match Allow precedence.

    urllib.robotparser is used separately for Crawl-delay/Request-rate, not its
    first-rule permission behavior. Unavailable/unreadable robots is handled
    before this function; no external I/O happens here.
    """
    groups = []
    agents, rules = [], []
    has_directive = False
    nonempty = False
    for original in body.splitlines():
        line = original.split("#", 1)[0].strip()
        if not line:
            continue
        nonempty = True
        if ":" not in line:
            continue
        key, value = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if has_directive:
                groups.append((agents, rules))
                agents, rules, has_directive = [], [], False
            agents.append(value.lower())
        elif agents:
            has_directive = True
            if key in {"allow", "disallow"} and value:
                rules.append((key == "allow", value))
    if agents:
        groups.append((agents, rules))
    if nonempty and not groups:
        return False  # a JSON/HTML/error message is not an empty robots file
    best = -1
    selected = []
    for agents, rules in groups:
        score = max((0 if a == "*" else len(a) for a in agents
                     if a == "*" or (a and a in AGENT.lower())), default=-1)
        if score > best:
            best, selected = score, list(rules)
        elif score == best and score >= 0:
            selected.extend(rules)
    target = _robot_path(urlsplit(url).path or "/")
    matches = []
    for allow, pattern in selected:
        pattern = _robot_path(pattern)
        anchored = pattern.endswith("$")
        raw = pattern[:-1] if anchored else pattern
        rx = "^" + ".*".join(re.escape(piece) for piece in raw.split("*")) + ("$" if anchored else "")
        if re.search(rx, target):
            matches.append((len(raw.replace("*", "").encode("utf-8")), allow))
    return max(matches)[1] if matches else True


def node_text(node) -> str:
    if node is None:
        return ""
    return re.sub(r"[ \t]+", " ", node.get_text("\n", strip=True)).strip()


def related(node) -> bool:
    for ancestor in [node, *list(node.parents)]:
        attrs = " ".join(str(ancestor.get(k, "")) for k in ("class", "id", "data-testid")) if getattr(ancestor, "attrs", None) else ""
        if BAD_ANCESTOR.search(attrs) or getattr(ancestor, "name", "") in {"nav", "footer", "aside"}:
            return True
    return False


def _schema_nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from _schema_nodes(item)
    elif isinstance(value, dict):
        kind = value.get("@type", [])
        kinds = {kind} if isinstance(kind, str) else set(kind) if isinstance(kind, list) and all(isinstance(x, str) for x in kind) else set()
        if kinds & {"ItemList", "BreadcrumbList", "Review", "AggregateRating"}:
            return
        if kinds & {"Car", "Vehicle", "Product"}:
            yield value
            return  # never extract the nested recommended product or seller
        for key in ("@graph", "mainEntity"):
            if key in value:
                yield from _schema_nodes(value[key])


def _description_string(value) -> str:
    if not isinstance(value, str):
        return ""
    soup = BeautifulSoup(value, "html.parser")
    for node in soup.select("script, style, template"):
        node.decompose()
    return node_text(soup)


def extract_detail(source: str, requested_url: str, html: str, max_chars: int = 20_000) -> Evidence:
    """Extract explicitly scoped seller description; never guess clean/paint count."""
    if not valid_url(source, requested_url):
        return Evidence("invalid_url", reason="host_or_path_not_allowed")
    if challenge(html):
        return Evidence("blocked", reason="human_verification_or_rate_limit")
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one("link[rel~='canonical'][href]")
    if canonical and not same_ad(source, requested_url, str(canonical.get("href", ""))):
        return Evidence("identity_mismatch", reason="canonical_points_to_other_page")
    metas = soup.select_one("meta[property='og:url'][content]")
    if metas and not same_ad(source, requested_url, str(metas.get("content", ""))):
        return Evidence("identity_mismatch", reason="og_url_points_to_other_page")
    for node in soup.select("script:not([type='application/ld+json']), style, nav, footer, aside, template"):
        node.decompose()
    main = soup.find("main") or soup
    titles = [h for h in main.find_all("h1") if not related(h) and node_text(h)]
    title = node_text(titles[0]) if len(titles) == 1 else ""
    generic = {"سایت دیوار", "دیوار", "باما", "کارنامه", "فروش خودرو", "ورود", "login"}
    if normalized(title) in generic:
        title = ""
    schemas = []
    for script in soup.select("script[type='application/ld+json']"):
        raw = script.string or script.get_text()
        if len(raw) > 500_000:
            continue
        try:
            schemas.extend(_schema_nodes(json.loads(raw)))
        except (ValueError, TypeError, RecursionError):
            continue
    selected = []
    for obj in schemas:
        ref = obj.get("url") or obj.get("@id")
        if isinstance(ref, str) and same_ad(source, requested_url, ref):
            selected.append(obj)
        elif not ref and len(schemas) == 1 and title and normalized(obj.get("name", "")) == normalized(title):
            selected.append(obj)
    if len(selected) > 1:
        signatures = {(str(x.get("name", "")), str(x.get("description", ""))) for x in selected}
        if len(signatures) != 1:
            return Evidence("ambiguous_detail", reason="multiple_primary_products")
        selected = selected[:1]

    descriptions: list[tuple[str, str]] = []
    attributes: list[tuple[str, str]] = []
    identity = "requested_url_and_unique_h1" if title else ""
    if selected:
        obj = selected[0]
        title = str(obj.get("name") or title)[:500]
        description = _description_string(obj.get("description"))
        if description:
            descriptions.append(("json_ld_primary_vehicle", description))
        identity = "primary_schema_matches_listing"
        for key in ("brand", "model", "vehicleModelDate", "mileageFromOdometer", "vehicleTransmission", "color"):
            value = obj.get(key)
            if isinstance(value, dict):
                value = " ".join(str(value[x]) for x in ("name", "value", "unitText", "unitCode") if x in value and isinstance(value[x], (str, int, float)))
            if isinstance(value, (str, int, float)):
                attributes.append((key, str(value)))
        for prop in obj.get("additionalProperty", []) if isinstance(obj.get("additionalProperty", []), list) else []:
            if isinstance(prop, dict) and isinstance(prop.get("name"), str) and normalized(prop["name"]) in LABELS and isinstance(prop.get("value"), (str, int, float)):
                attributes.append((prop["name"], str(prop["value"])))
        # Offers retain unit/currency/evidence only. NO price normalization here.
        offers = obj.get("offers")
        if isinstance(offers, dict):
            for key in ("price", "priceCurrency", "availability"):
                if isinstance(offers.get(key), (str, int, float)):
                    attributes.append(("offer." + key, str(offers[key])))
    if title:
        scopes = main.select("[itemtype$='/Car'], [itemtype$='/Vehicle'], [itemtype$='/Product']")
        for scope in scopes:
            itemid = scope.get("itemid")
            if itemid and not same_ad(source, requested_url, str(itemid)):
                continue
            if related(scope):
                continue
            name = node_text(scope.select_one("[itemprop='name']"))
            if not itemid and name and normalized(name) != normalized(title):
                continue
            if not itemid and len(scopes) > 1:
                continue
            for node in scope.select("[itemprop='description']"):
                if not related(node) and node.name != "meta":
                    descriptions.append(("scoped_microdata_description", node_text(node)))
        # Source-specific selectors live only within the unique listing page.
        for selector in SELECTORS.get(source, ()):
            nodes = [n for n in main.select(selector) if not related(n)]
            if len(nodes) == 1:
                descriptions.append(("dedicated_dom_description", node_text(nodes[0])))
                break
        # Heading fallback accepts only a direct neighboring paragraph/container.
        for heading in main.find_all(["h2", "h3", "h4"]):
            if normalized(node_text(heading)) not in HEADINGS or related(heading):
                continue
            nxt = heading.find_next_sibling()
            if nxt is not None and nxt.name in {"p", "div", "section"} and not related(nxt) and not nxt.find(["h1", "h2", "h3", "table"]):
                # Reject containers with links to other listings rather than a seller paragraph.
                if not any(valid_url(source, a.get("href", "")) for a in nxt.find_all("a", href=True)):
                    descriptions.append(("labeled_description_section", node_text(nxt)))
        for dl in main.find_all("dl"):
            if related(dl):
                continue
            for dt in dl.find_all("dt", recursive=False):
                dd = dt.find_next_sibling("dd")
                label = node_text(dt)
                if dd is not None and normalized(label) in LABELS:
                    attributes.append((label, node_text(dd)))
        for tr in main.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) == 2 and not related(tr) and normalized(node_text(cells[0])) in LABELS:
                attributes.append((node_text(cells[0]), node_text(cells[1])))
        if source == "divar":
            for row in main.select(".kt-base-row"):
                label = node_text(row.select_one(".kt-base-row__title"))
                value = node_text(row.select_one(".kt-unexpandable-row__value"))
                if label and value and normalized(label) in LABELS and not related(row):
                    attributes.append((label, value))
    if not title or not identity:
        return Evidence("identity_not_established", reason="no_single_listing_identity")
    # Preserve all differing explicit description evidence instead of dropping a
    # shorter damage disclosure. De-duplicate exact repeats, not contradicting text.
    unique, mechanisms = [], []
    for method, text in descriptions:
        if len(text.strip()) >= 8 and normalized(text) not in {normalized(t) for t in unique}:
            unique.append(text.strip())
            mechanisms.append(method)
    description = "\n\n".join(unique)
    field_truncated = len(attributes) > 80 or any(len(k) > 100 or len(v) > 2000 for k, v in attributes)
    attrs = tuple(dict.fromkeys((k[:100], v[:2000]) for k, v in attributes[:80] if k.strip() and v.strip()))
    if not description:
        return Evidence("description_not_found", title=title, attributes=attrs,
                        identity_basis=identity, fields_truncated=field_truncated,
                        reason="missing_or_javascript_only_description")
    truncated = len(description) > max_chars
    return Evidence("captured" if not truncated and not field_truncated else "partial_truncated",
                    title, description[:max_chars], attrs, "+".join(sorted(set(mechanisms))),
                    identity, truncated, field_truncated)


SCHEMA = """
CREATE TABLE IF NOT EXISTS detail_pages (
 source_key TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
 url TEXT NOT NULL, search_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
 status TEXT NOT NULL, evidence_json TEXT NOT NULL, attempted_at REAL NOT NULL,
 fetched_at REAL NOT NULL, next_fetch_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_detail_source ON detail_pages(source, attempted_at);
CREATE TABLE IF NOT EXISTS detail_policy (
 source TEXT PRIMARY KEY, last_probe REAL NOT NULL DEFAULT 0,
 next_probe REAL NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS robots_cache (
 origin TEXT PRIMARY KEY, body TEXT NOT NULL, status INTEGER NOT NULL,
 fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS detail_http_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, kind TEXT NOT NULL,
 created_at REAL NOT NULL
);
"""


def current_candidates(runtime: Path, sources: Iterable[str], since: str) -> list[Candidate]:
    """Only observations from this receiving run, including useful rejects.

    No historical market record is reclassified/deleted here. Finance-filtered
    records are not fetched. Selection is not based on low prices alone, allowing
    future reference samples as well as potential candidates to be completed.
    """
    sources = tuple(s for s in sources if s in HOSTS)
    path = runtime / "listing_observations.sqlite3"
    if not path.is_file() or not sources or not timestamp(since):
        return []
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        query = ("SELECT * FROM listing_observations WHERE source IN (" + ",".join("?" for _ in sources) + ") "
                 "AND julianday(last_seen)>=julianday(?) ORDER BY last_seen DESC,source_key LIMIT 1200")
        rows = connection.execute(query, (*sources, since)).fetchall()
    finally:
        connection.close()
    found = []
    for row in rows:
        url = valid_url(row["source"], row["url"])
        text = normalized(row["received_text"])
        if (not url or row["text_truncated"] or row["received_scope"] != "search_card_text"
                or str(row["reason"]).startswith("blocked_phrase") or any(x in text for x in FINANCE)):
            continue
        found.append(Candidate(row["source"], row["source_ad_id"], url, row["title"],
                               row["text_sha256"], row["last_seen"], row["outcome"]))
    return found


@dataclass
class StageReport:
    http_requests: int = 0
    page_requests: int = 0
    captured: int = 0
    cache_hits: int = 0
    skipped: Counter = field(default_factory=Counter)
    outcomes: Counter = field(default_factory=Counter)
    blocks: dict[str, float] = field(default_factory=dict)
    stopped: str = ""

    def public(self) -> dict:
        return {"version": VERSION, "http_requests": self.http_requests,
                "page_requests": self.page_requests, "captured": self.captured,
                "cache_hits": self.cache_hits, "skipped": dict(self.skipped),
                "outcomes": dict(self.outcomes), "blocked_sources": sorted(self.blocks),
                "stopped": self.stopped, "mode": "detail_evidence_only_no_listing_promotion"}


class DetailRunner:
    def __init__(self, runtime: Path, *, policy: Policy | None = None, transport=None,
                 now: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, randomizer=None):
        self.runtime = Path(runtime)
        self.policy = policy or Policy.from_environment()
        self.transport = transport
        self.own_transport = transport is None
        self.now, self.monotonic, self.sleep = now, monotonic, sleep
        self.rng = randomizer or random.SystemRandom()
        self.report = StageReport()
        self.db = None
        self.deadline = 0.0
        self.page_by_source = Counter()
        self.min_robot_delays: dict[str, float] = {}

    def _policy_row(self, source):
        row = self.db.execute("SELECT * FROM detail_policy WHERE source=?", (source,)).fetchone()
        return dict(row) if row else {"last_probe": 0, "next_probe": 0, "blocked_until": 0}

    def _count_day(self, source, kind=None):
        q = "SELECT COUNT(*) FROM detail_http_events WHERE source=? AND created_at>?"
        params = [source, self.now() - 86400]
        if kind:
            q += " AND kind=?"
            params.append(kind)
        return self.db.execute(q, params).fetchone()[0]

    def _block(self, source, headers):
        until = self.now() + self.policy.cooldown_hours * 3600
        value = headers.get("retry-after", "").strip()
        try:
            if value.isdigit():
                until = max(until, self.now() + int(value))
            elif value:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is not None:
                    until = max(until, parsed.timestamp())
        except (ValueError, TypeError, OverflowError):
            pass
        # Preserve longer server-provided waits; never reduce a stored block.
        until = max(until, self._policy_row(source)["blocked_until"])
        self.db.execute("INSERT INTO detail_policy(source,blocked_until) VALUES (?,?) "
                        "ON CONFLICT(source) DO UPDATE SET blocked_until=MAX(blocked_until,excluded.blocked_until)", (source, until))
        self.db.commit()
        self.report.blocks[source] = until
        self.report.stopped = "blocked_stop_all_details"

    def _request(self, source, url, kind) -> HttpPage:
        if self.report.stopped:
            return HttpPage(0, error=self.report.stopped)
        if self.report.http_requests >= self.policy.max_http or self._count_day(source) >= self.policy.max_http_per_source_day:
            self.report.skipped["http_budget"] += 1
            return HttpPage(0, error="http_budget")
        delay = max(self.rng.uniform(self.policy.delay_min, self.policy.delay_max), self.min_robot_delays.get(source, 0))
        if self.monotonic() + delay + self.policy.minimum_seconds_for_request > self.deadline:
            self.report.skipped["time_budget"] += 1
            return HttpPage(0, error="time_budget")
        LOGGER.info("[ACCURATE-SYSTEM] DetailPacing source=%s purpose=%s delay_seconds=%.2f", source, kind, delay)
        self.sleep(delay)
        # Durable reservation happens BEFORE HTTP, including failed attempts.
        self.db.execute("INSERT INTO detail_http_events(source,kind,created_at) VALUES (?,?,?)", (source, kind, self.now()))
        self.db.commit()
        self.report.http_requests += 1
        if kind == "detail":
            self.report.page_requests += 1
            self.page_by_source[source] += 1
        if self.transport is None:
            self.transport = PublicTransport()
        try:
            result = self.transport.get(url, self.policy.max_bytes if kind == "detail" else 256_000)
        except Exception as exc:
            # Record class only; a request exception string can include a URL/token.
            result = HttpPage(0, error=type(exc).__name__)
        if result.status in {403, 429} or (result.body and challenge(result.body)):
            self._block(source, result.headers)
            return HttpPage(result.status, headers=result.headers, error="blocked")
        return result

    def _robots_allowed(self, candidate) -> tuple[bool, str]:
        p = urlsplit(candidate.url)
        origin = f"https://{p.hostname}"
        cached = self.db.execute("SELECT * FROM robots_cache WHERE origin=?", (origin,)).fetchone()
        if cached and self.now() - cached["fetched_at"] < 86400:
            page = HttpPage(cached["status"], cached["body"])
        else:
            if self.policy.max_http - self.report.http_requests < 2:
                return False, "robots_plus_detail_budget"
            page = self._request(candidate.source, origin + "/robots.txt", "robots")
            if page.error:
                return False, "robots_" + page.error
            if page.status == 200 and ("<html" in page.body.lower() or "<!doctype html" in page.body.lower()):
                return False, "robots_unreadable"
            if page.status not in {200, 404, 410}:
                return False, "robots_unavailable_no_retry"
            self.db.execute("INSERT OR REPLACE INTO robots_cache(origin,body,status,fetched_at) VALUES (?,?,?,?)",
                            (origin, page.body, page.status, self.now()))
            self.db.commit()
        if page.status in {404, 410}:
            return True, "robots_missing"
        parser = RobotFileParser()
        parser.parse(page.body.splitlines())
        if not robots_permission(page.body, candidate.url):
            return False, "robots_disallowed"
        delay = parser.crawl_delay(AGENT) or 0
        rate = parser.request_rate(AGENT)
        if rate and rate.requests > 0:
            delay = max(delay, rate.seconds / rate.requests)
        self.min_robot_delays[candidate.source] = max(self.policy.delay_min, float(delay))
        return True, "robots_allowed"

    def _save(self, candidate, evidence, fetched=False):
        now = self.now()
        # Failed/partial evidence is explicit. Do not silently reuse old success.
        wait = max(6 * 3600, self.policy.refresh_hours * 3600 if evidence.status == "captured" else 6 * 3600)
        self.db.execute("INSERT OR REPLACE INTO detail_pages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (candidate.key, candidate.source, candidate.source_id, candidate.url,
                         candidate.search_hash, candidate.observed_at, evidence.status,
                         json.dumps(asdict(evidence), ensure_ascii=False), now,
                         now if fetched else 0, now + wait))
        self.db.commit()
        self.report.outcomes[candidate.source + ":" + evidence.status] += 1
        if evidence.status == "captured":
            self.report.captured += 1
        LOGGER.info("[ACCURATE-SYSTEM] DetailEvidence source=%s ad_ref=%s status=%s "
                    "description_chars=%s attributes=%s extraction=%s evidence_only=true",
                    candidate.source, candidate.public_ref, evidence.status, len(evidence.description),
                    len(evidence.attributes), evidence.extraction)

    def collect(self, candidates: Iterable[Candidate], *, source_intervals: dict[str, int],
                external_blocks: dict[str, float] | None = None, max_seconds: float | None = None) -> StageReport:
        if not self.policy.enabled:
            self.report.stopped = "disabled"
            return self.report
        budget = min(self.policy.max_seconds, max_seconds) if max_seconds is not None else self.policy.max_seconds
        if budget < self.policy.minimum_seconds_for_request:
            self.report.stopped = "insufficient_run_time"
            return self.report
        self.deadline = self.monotonic() + budget
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.runtime / DATABASE_NAME, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.executescript(SCHEMA)
            pending: dict[str, list[tuple[float, Candidate]]] = {}
            seen = set()
            for c in candidates:
                if c.key in seen:
                    self.report.skipped["duplicate_candidate"] += 1
                    continue
                seen.add(c.key)
                if not valid_url(c.source, c.url):
                    self.report.skipped["url_not_allowed"] += 1
                    continue
                row = self.db.execute("SELECT * FROM detail_pages WHERE source_key=?", (c.key,)).fetchone()
                if row and row["status"] == "captured" and row["url"] == c.url and row["search_hash"] == c.search_hash and self.now() < row["next_fetch_at"]:
                    self.report.cache_hits += 1
                    continue
                if row and self.now() < row["next_fetch_at"]:
                    self.report.skipped["entry_cooldown"] += 1
                    continue
                pending.setdefault(c.source, []).append((row["attempted_at"] if row else 0, c))
            # Least-recently probed SOURCE first, not always Divar or alphabetical.
            source_order = sorted(pending, key=lambda s: (self._policy_row(s)["last_probe"],
                                                          hashlib.sha256(s.encode()).hexdigest()))
            for source in source_order:
                if self.report.stopped or self.report.page_requests >= self.policy.max_pages:
                    break
                state = self._policy_row(source)
                if max(state["blocked_until"], (external_blocks or {}).get(source, 0)) > self.now():
                    self.report.skipped["source_blocked"] += 1
                    continue
                if state["next_probe"] > self.now():
                    self.report.skipped["source_interval"] += 1
                    continue
                if self._count_day(source, "detail") >= self.policy.max_pages_per_source_day:
                    self.report.skipped["daily_page_budget"] += 1
                    continue
                if self._count_day(source) >= self.policy.max_http_per_source_day:
                    self.report.skipped["daily_http_budget"] += 1
                    continue
                # Finance screening is upstream; no price ranking here. Select an
                # unseen/least-probed ad from either accepted candidates or rejects
                # requiring more evidence, so future references are not excluded.
                _, c = min(pending[source], key=lambda pair: (pair[0], -timestamp(pair[1].observed_at), pair[1].public_ref))
                interval = max(6, int(source_intervals.get(source, 12))) * 3600
                self.db.execute("INSERT INTO detail_policy(source,last_probe,next_probe) VALUES (?,?,?) "
                                "ON CONFLICT(source) DO UPDATE SET last_probe=excluded.last_probe,next_probe=excluded.next_probe",
                                (source, self.now(), self.now() + interval))
                self.db.commit()
                permitted, reason = self._robots_allowed(c)
                if not permitted:
                    self._save(c, Evidence("skipped", reason=reason))
                    self.report.skipped[reason] += 1
                    continue
                if self.page_by_source[source] >= self.policy.max_pages_per_source:
                    continue
                page = self._request(source, c.url, "detail")
                if page.error:
                    self._save(c, Evidence("blocked" if page.error == "blocked" else "fetch_failed", reason=page.error))
                    continue
                if page.status != 200:
                    status = "gone" if page.status in {404, 410} else "redirect_not_followed" if 300 <= page.status < 400 else "http_error"
                    self._save(c, Evidence(status, reason="http_" + str(page.status)))
                    continue
                if not any(x in page.headers.get("content-type", "").lower() for x in ("text/html", "application/xhtml+xml")):
                    self._save(c, Evidence("unsupported_content_type"))
                    continue
                evidence = extract_detail(source, c.url, page.body, self.policy.max_description_chars)
                if evidence.status == "blocked":
                    self._block(source, page.headers)
                self._save(c, evidence, fetched=True)
            # Count-bounded evidence cache only; never touch aa_listings/sent rows.
            self.db.execute("DELETE FROM detail_pages WHERE source_key IN (SELECT source_key FROM detail_pages ORDER BY attempted_at DESC,source_key LIMIT -1 OFFSET 2000)")
            self.db.execute("DELETE FROM detail_http_events WHERE created_at<?", (self.now() - 7 * 86400,))
            self.db.commit()
        finally:
            self.db.close()
            self.db = None
            if self.own_transport and self.transport is not None:
                self.transport.close()
        return self.report


def capture_selected_details(*, runtime: Path, selected_sources: Iterable[str], since: str,
                             source_intervals: dict[str, int], external_blocks=None,
                             policy: Policy | None = None, max_seconds: float | None = None,
                             transport=None, runner_kwargs=None) -> StageReport:
    policy = policy or Policy.from_environment()
    if not policy.enabled:
        report = StageReport(stopped="disabled")
    else:
        candidates = current_candidates(Path(runtime), selected_sources, since)
        if not candidates:
            report = StageReport(stopped="no_current_web_observations")
        else:
            runner = DetailRunner(Path(runtime), policy=policy, transport=transport, **(runner_kwargs or {}))
            report = runner.collect(candidates, source_intervals=source_intervals,
                                    external_blocks=external_blocks, max_seconds=max_seconds)
    LOGGER.info("[ACCURATE-SYSTEM] DetailStageSummary %s", json.dumps(report.public(), sort_keys=True))
    return report


# Small deployment self-test uses a fake transport. Extended tests ship separately.
def run_self_test() -> None:
    import tempfile
    from unittest.mock import patch
    u = "https://divar.ir/v/test/Abc123"
    html = ('<link rel="canonical" href="' + u + '"><main><h1>پژو ۲۰۶ تیپ ۵</h1>'
            '<div class="kt-description-row__text">دو قطعه رنگ، شاسی سالم؛ کارکرد ۱۲۰ هزار کیلومتر.</div>'
            '<dl><dt>وضعیت شاسی</dt><dd>سالم</dd></dl></main>')
    evidence = extract_detail("divar", u, html)
    if evidence.status != "captured" or "شاسی سالم" not in evidence.description:
        raise AssertionError("Dedicated description was not captured")
    if extract_detail("divar", u, '<main><h1>پژو ۲۰۶</h1></main>').status != "description_not_found":
        raise AssertionError("Missing description must not be guessed")
    if valid_url("divar", "https://divar.ir.evil.invalid/v/a/b"):
        raise AssertionError("Host allowlist failed")
    if extract_detail("divar", u, '<main>verify you are human</main>').status != "blocked":
        raise AssertionError("Challenge detection failed")
    class Fake:
        def __init__(self): self.calls = []
        def get(self, url, maximum):
            self.calls.append(url)
            return HttpPage(200, "User-agent: *\nAllow: /\n", {"content-type": "text/plain"}) if url.endswith("robots.txt") else HttpPage(200, html, {"content-type": "text/html"})
    c = Candidate("divar", "Abc123", u, "test", "a" * 64, iso(1_790_000_000), "rejected")
    with tempfile.TemporaryDirectory() as d, patch.object(requests.sessions.Session, "request", side_effect=AssertionError("self-test network forbidden")):
        fake = Fake()
        kwargs = {"now": lambda: 1_790_000_000, "monotonic": lambda: 1000.0, "sleep": lambda delay: None}
        r = DetailRunner(Path(d), transport=fake, **kwargs).collect([c], source_intervals={"divar": 12})
        if r.captured != 1 or len(fake.calls) != 2:
            raise AssertionError("Fetch and robots budget integration failed")
        second = DetailRunner(Path(d), transport=fake, **kwargs).collect([c], source_intervals={"divar": 12})
        if second.cache_hits != 1 or len(fake.calls) != 2:
            raise AssertionError("Cache must avoid extra requests")
    print("site detail reader self-test: OK")
    print("site detail reader version=1.0.0 mode=evidence_only")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.parse_args()
    run_self_test()
