#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

RUNTIME = Path(os.getenv('ACCURATE_RUNTIME_DIR', 'accurate_average_runtime'))
LOG_PATH = RUNTIME / 'accurate_average.log'
DB_PATH = RUNTIME / 'accurate_average.db'
OUT_PATH = RUNTIME / 'diagnostics' / 'select_carr_readonly_deal_diagnosis.json'

DECISION_RE = re.compile(
    r'SharedDecision source=(?P<source>\S+) source_key=(?P<source_key>\S+) '
    r'legacy_reason=(?P<legacy_reason>\S+) legacy_samples=(?P<legacy_samples>\d+) '
    r'reference_sources=(?P<reference_sources>\{.*?\}) '
    r'shadow_group_rows=(?P<shadow_group_rows>\d+) '
    r'new_engine_reason=(?P<new_engine_reason>\S+) new_engine_samples=(?P<new_engine_samples>\d+) '
    r'new_engine_details=(?P<new_engine_details>\S+) .*?already_sent=(?P<already_sent>\S+) '
    r'allow_send=(?P<allow_send>\S+)'
)

SUMMARY_RE = re.compile(
    r'SharedBatchComplete candidates=(?P<candidates>\d+) deals=(?P<deals>\d+) messages=(?P<messages>\d+)'
)


def read_last_run_lines() -> list[str]:
    if not LOG_PATH.exists():
        raise SystemExit(f'Missing log: {LOG_PATH}')
    lines = LOG_PATH.read_text(encoding='utf-8', errors='replace').splitlines()
    starts = [i for i, line in enumerate(lines) if 'RuntimeConfig ' in line]
    return lines[starts[-1]:] if starts else lines[-1500:]


def parse_log(lines: list[str]) -> dict:
    legacy_reasons = Counter()
    new_reasons = Counter()
    new_details = Counter()
    sources = Counter()
    sample_dist = Counter()
    decisions = []
    batch = None

    for line in lines:
        m = DECISION_RE.search(line)
        if m:
            d = m.groupdict()
            d['legacy_samples'] = int(d['legacy_samples'])
            d['new_engine_samples'] = int(d['new_engine_samples'])
            try:
                d['reference_sources'] = json.loads(d['reference_sources'])
            except Exception:
                d['reference_sources'] = {}
            decisions.append(d)
            legacy_reasons[d['legacy_reason']] += 1
            new_reasons[d['new_engine_reason']] += 1
            if d['new_engine_details'] != 'none':
                new_details[d['new_engine_details']] += 1
            sources[d['source']] += 1
            sample_dist[str(d['legacy_samples'])] += 1

        sm = SUMMARY_RE.search(line)
        if sm:
            batch = {k: int(v) for k, v in sm.groupdict().items()}

    return {
        'batch': batch,
        'decision_count': len(decisions),
        'legacy_reason_counts': dict(legacy_reasons),
        'new_engine_reason_counts': dict(new_reasons),
        'new_engine_detail_counts': dict(new_details),
        'candidate_sources': dict(sources),
        'legacy_sample_count_distribution': dict(sample_dist),
        'decisions': decisions,
    }


def closest_groups(limit: int = 15) -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON')
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'aa_listings' not in tables:
            return []

        rows = con.execute(
            '''
            SELECT source_key, source, brand, model, trim, model_year, price,
                   comparison_key, last_seen, body_condition, mileage
            FROM aa_listings
            ORDER BY last_seen DESC
            LIMIT 250
            '''
        ).fetchall()

        out = []
        for row in rows:
            peers = con.execute(
                '''
                SELECT price, source
                FROM aa_listings
                WHERE comparison_key=? AND source_key<>?
                ORDER BY last_seen DESC
                LIMIT 10
                ''',
                (row['comparison_key'], row['source_key']),
            ).fetchall()
            if not peers:
                continue
            avg = sum(int(p['price']) for p in peers) / len(peers)
            discount = ((avg - int(row['price'])) / avg * 100.0) if avg else 0.0
            out.append({
                'source_key': row['source_key'],
                'source': row['source'],
                'vehicle': ' '.join(str(x) for x in (row['brand'], row['model'], row['trim'], row['model_year']) if x not in (None, '')),
                'price': int(row['price']),
                'peer_count': len(peers),
                'retained_peer_average': round(avg),
                'retained_discount_percent': round(discount, 2),
                'body_condition': row['body_condition'],
                'mileage': row['mileage'],
                'note': 'diagnostic_only_not_production_decision',
            })

        out.sort(key=lambda x: (-x['retained_discount_percent'], -x['peer_count']))
        return out[:limit]
    finally:
        con.close()


def main() -> int:
    report = parse_log(read_last_run_lines())
    report['closest_retained_groups'] = closest_groups()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print('=== SELECT CARR READ-ONLY DEAL DIAGNOSIS ===')
    print(json.dumps({
        'batch': report['batch'],
        'decision_count': report['decision_count'],
        'legacy_reason_counts': report['legacy_reason_counts'],
        'new_engine_reason_counts': report['new_engine_reason_counts'],
        'new_engine_detail_counts': report['new_engine_detail_counts'],
        'legacy_sample_count_distribution': report['legacy_sample_count_distribution'],
        'report_path': str(OUT_PATH),
    }, ensure_ascii=False, indent=2))
    print('=== END DIAGNOSIS ===')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
