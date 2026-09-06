#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import sys
from typing import Any


MIN_DEAL_PERCENT = 2.0
MAX_DEAL_PERCENT = 15.0


def _in_range(value: float) -> bool:
    return (
        MIN_DEAL_PERCENT
        <= float(value)
        <= MAX_DEAL_PERCENT
    )


# ============================================================
# Car Price Bot
# ============================================================

def run_car_price_bot() -> None:
    import main as car_bot

    original_load_config = car_bot.load_config

    def load_config_with_policy() -> dict[str, Any]:
        config = original_load_config()

        config["deal_threshold_percent"] = (
            MIN_DEAL_PERCENT
        )

        config["deal_max_discount_percent"] = (
            MAX_DEAL_PERCENT
        )

        return config

    car_bot.load_config = load_config_with_policy

    print(
        "[UNIFIED-DEAL-POLICY] "
        "System=car_price_bot "
        "Range=2%-15%"
    )

    car_bot.main()


# ============================================================
# Divar Deal Service
# ============================================================

def run_divar_service() -> None:
    import divar_service.deal_finder as deal_finder

    # حداقل Deal سیستم دیوار را بدون تغییر فایل اصلی
    # روی 2 درصد قرار می‌دهیم.
    if hasattr(
        deal_finder,
        "MIN_DISCOUNT_PERCENT",
    ):
        deal_finder.MIN_DISCOUNT_PERCENT = (
            MIN_DEAL_PERCENT
        )

    # اگر نسخه فعلی فایل دارای سقف باشد،
    # آن را نیز روی 15 قرار می‌دهیم.
    if hasattr(
        deal_finder,
        "MAX_DISCOUNT_PERCENT",
    ):
        deal_finder.MAX_DISCOUNT_PERCENT = (
            MAX_DEAL_PERCENT
        )

    original_find_deals = (
        deal_finder.find_deals
    )

    def find_deals_with_policy(ads):
        candidates = original_find_deals(
            ads
        )

        filtered = [
            candidate
            for candidate in candidates
            if _in_range(
                candidate.diff_percent
            )
        ]

        removed = (
            len(candidates)
            - len(filtered)
        )

        if removed:
            print(
                "[UNIFIED-DEAL-POLICY] "
                f"System=divar "
                f"RejectedOutsideRange={removed}"
            )

        return filtered

    deal_finder.find_deals = (
        find_deals_with_policy
    )

    # analyzer.py از find_deals مستقیم Import می‌کند.
    # بنابراین بعد از Policy آن را Import می‌کنیم.
    import divar_service.analyzer as analyzer

    analyzer.find_deals = (
        find_deals_with_policy
    )

    # برای سازگاری تنظیمات موجود
    os.environ[
        "DIVAR_MIN_DEAL_PERCENT"
    ] = str(
        MIN_DEAL_PERCENT
    )

    os.environ[
        "DIVAR_MAX_DEAL_PERCENT"
    ] = str(
        MAX_DEAL_PERCENT
    )

    print(
        "[UNIFIED-DEAL-POLICY] "
        "System=divar "
        "Range=2%-15%"
    )

    from divar_service.app import main

    main()


# ============================================================
# Accurate Average System B
# ============================================================

def run_accurate_average() -> None:
    os.environ[
        "ACCURATE_DEAL_THRESHOLD_PERCENT"
    ] = str(
        MIN_DEAL_PERCENT
    )

    import accurate_average_service as service

    original_evaluate = (
        service.evaluate_against_history
    )

    def evaluate_with_policy(
        store,
        listing,
        threshold_percent,
    ):
        deal = original_evaluate(
            store,
            listing,
            MIN_DEAL_PERCENT,
        )

        if deal is None:
            return None

        if not _in_range(
            deal.discount_percent
        ):
            print(
                "[UNIFIED-DEAL-POLICY] "
                "System=accurate_average "
                "RejectedOutsideRange "
                f"discount="
                f"{deal.discount_percent:.2f}%"
            )

            return None

        return deal

    service.evaluate_against_history = (
        evaluate_with_policy
    )

    print(
        "[UNIFIED-DEAL-POLICY] "
        "System=accurate_average "
        "Range=2%-15%"
    )

    service.main()


# ============================================================
# Main Router
# ============================================================

def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: "
            "python unified_deal_policy_runner.py "
            "[car_price_bot|divar|accurate_average]"
        )

    target = (
        sys.argv[1]
        .strip()
        .lower()
    )

    if target == "car_price_bot":
        run_car_price_bot()
        return

    if target == "divar":
        run_divar_service()
        return

    if target == "accurate_average":
        run_accurate_average()
        return

    raise SystemExit(
        f"Unknown system: {target}"
    )


if __name__ == "__main__":
    main()
