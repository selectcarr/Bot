from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests import Response

from divar_service.config.settings import Settings


LOGGER = logging.getLogger(
    "divar_service.divar_client"
)


DEFAULT_SEARCH_URL = (
    "https://divar.ir/s/tehran/car"
)


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6"
    ),
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


BLOCK_STATUS_CODES = {
    403,
    429,
}


VISIBLE_BLOCK_MARKERS = (
    "لطفاً تأیید کنید ربات نیستید",
    "لطفا تایید کنید ربات نیستید",
    "تأیید کنید ربات نیستید",
    "تایید کنید ربات نیستید",
    "درخواست های بیش از حد",
    "درخواست‌های بیش از حد",
    "تعداد درخواست های شما بیش از حد",
    "تعداد درخواست‌های شما بیش از حد",
    "دسترسی شما موقتاً محدود شده",
    "دسترسی شما محدود شده",
    "فعالیت غیرعادی",
    "verify you are human",
    "verify that you are human",
    "are you a robot",
    "unusual traffic",
    "too many requests",
    "access denied",
)


class DivarClientError(RuntimeError):
    """
    Base error for Divar requests.
    """


class DivarBlockedError(DivarClientError):
    """
    Raised only when strong evidence of blocking
    or verification is detected.
    """


class DivarClient:
    def __init__(
        self,
        settings: Settings,
    ) -> None:
        self.settings = settings

        self.search_url = os.getenv(
            "DIVAR_SEARCH_URL",
            DEFAULT_SEARCH_URL,
        ).strip()

        if not self.search_url:
            raise ValueError(
                "DIVAR_SEARCH_URL cannot be empty."
            )

        self.session = requests.Session()

        self.session.headers.update(
            DEFAULT_HEADERS
        )

        self.cookies_path = Path(
            settings.cookies_path
        )

        self.diagnostics_directory = (
            self.cookies_path.parent
            / "diagnostics"
        )

        self._load_cookies()

    def fetch_search_page(
        self,
        page_number: int,
    ) -> str:
        """
        Fetch one Divar vehicle search page.
        """
        if not (
            1
            <= page_number
            <= self.settings.max_pages
        ):
            raise ValueError(
                "page_number is outside "
                "the allowed range."
            )

        params: dict[str, Any] = {}

        if page_number > 1:
            params["page"] = (
                page_number - 1
            )

        response = self._get(
            url=self.search_url,
            params=params,
            expect_search_results=True,
        )

        return response.text

    def fetch_ad_page(
        self,
        url: str,
    ) -> str:
        """
        Fetch one advertisement detail page.

        This method is currently not used by the
        incremental collector.
        """
        clean_url = url.strip()

        if not clean_url.startswith(
            "https://divar.ir/v/"
        ):
            raise ValueError(
                "Invalid Divar advertisement URL."
            )

        response = self._get(
            url=clean_url,
            expect_search_results=False,
        )

        return response.text

    def sleep_after_page(
        self,
        page_number: int,
    ) -> float:
        """
        Wait before requesting the next page.
        """
        if page_number == 1:
            minimum = (
                self.settings
                .first_page_delay_min
            )

            maximum = (
                self.settings
                .first_page_delay_max
            )

        else:
            minimum = (
                self.settings
                .next_page_delay_min
            )

            maximum = (
                self.settings
                .next_page_delay_max
            )

        delay = random.uniform(
            minimum,
            maximum,
        )

        time.sleep(
            delay
        )

        return delay

    def close(self) -> None:
        self._save_cookies()
        self.session.close()

    def __enter__(
        self,
    ) -> DivarClient:
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        self.close()

    def _get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        expect_search_results: bool = False,
    ) -> Response:
        try:
            response = self.session.get(
                url=url,
                params=params,
                timeout=(
                    self.settings
                    .request_timeout_seconds
                ),
                allow_redirects=True,
            )

        except requests.Timeout as exc:
            raise DivarClientError(
                "Divar request timed out."
            ) from exc

        except requests.RequestException as exc:
            raise DivarClientError(
                f"Divar request failed: {exc}"
            ) from exc

        self._save_cookies()

        diagnostics = self._inspect_response(
            response=response,
            expect_search_results=(
                expect_search_results
            ),
        )

        self._log_diagnostics(
            diagnostics
        )

        if (
            response.status_code
            in BLOCK_STATUS_CODES
        ):
            diagnostic_path = (
                self._save_diagnostic_response(
                    response=response,
                    diagnostics=diagnostics,
                    reason="blocking_status_code",
                )
            )

            raise DivarBlockedError(
                "Divar returned blocking HTTP "
                f"status {response.status_code}. "
                f"Diagnostic: {diagnostic_path}"
            )

        try:
            response.raise_for_status()

        except requests.HTTPError as exc:
            diagnostic_path = (
                self._save_diagnostic_response(
                    response=response,
                    diagnostics=diagnostics,
                    reason="unexpected_http_status",
                )
            )

            raise DivarClientError(
                "Divar returned HTTP status "
                f"{response.status_code}. "
                f"Diagnostic: {diagnostic_path}"
            ) from exc

        content_type = str(
            diagnostics["content_type"]
        ).lower()

        if (
            content_type
            and "text/html" not in content_type
        ):
            diagnostic_path = (
                self._save_diagnostic_response(
                    response=response,
                    diagnostics=diagnostics,
                    reason="unexpected_content_type",
                )
            )

            raise DivarClientError(
                "Divar response is not HTML. "
                f"Content-Type: {content_type}. "
                f"Diagnostic: {diagnostic_path}"
            )

        detected_marker = diagnostics.get(
            "detected_marker"
        )

        ad_link_count = int(
            diagnostics.get(
                "ad_link_count",
                0,
            )
        )

        # وجود لینک آگهی نشان می‌دهد صفحه نتایج
        # قابل استفاده است؛ حتی اگر داخل اسکریپت‌ها
        # واژه‌هایی مثل captcha وجود داشته باشد.
        if (
            expect_search_results
            and ad_link_count > 0
        ):
            return response

        # در صفحات جزئیات، نبود لینک /v/ طبیعی است.
        if (
            not expect_search_results
            and not detected_marker
        ):
            return response

        # بلاک فقط وقتی پذیرفته می‌شود که نشانه
        # در متن قابل مشاهده صفحه پیدا شده باشد.
        if detected_marker:
            diagnostic_path = (
                self._save_diagnostic_response(
                    response=response,
                    diagnostics=diagnostics,
                    reason="visible_block_marker",
                )
            )

            raise DivarBlockedError(
                "A visible blocking or verification "
                "message was detected. "
                f"Marker: {detected_marker}. "
                f"Diagnostic: {diagnostic_path}"
            )

        if expect_search_results:
            diagnostic_path = (
                self._save_diagnostic_response(
                    response=response,
                    diagnostics=diagnostics,
                    reason="no_advertisement_links",
                )
            )

            raise DivarClientError(
                "Divar returned HTML but no "
                "advertisement links were found. "
                "The page structure may have changed "
                "or the response may require JavaScript. "
                f"Diagnostic: {diagnostic_path}"
            )

        return response

    def _inspect_response(
        self,
        response: Response,
        expect_search_results: bool,
    ) -> dict[str, object]:
        html = response.text

        soup = BeautifulSoup(
            html,
            "html.parser",
        )

        title = ""

        if soup.title is not None:
            title = soup.title.get_text(
                " ",
                strip=True,
            )[:300]

        advertisement_links = {
            str(link.get("href", "")).strip()
            for link in soup.select(
                'a[href*="/v/"]'
            )
            if str(
                link.get("href", "")
            ).strip()
        }

        # متن اسکریپت‌ها نباید باعث تشخیص
        # اشتباه کپچا شود.
        for element in soup.find_all(
            [
                "script",
                "style",
                "noscript",
                "svg",
            ]
        ):
            element.decompose()

        visible_text = " ".join(
            soup.stripped_strings
        )

        normalized_visible_text = (
            visible_text
            .lower()
            .replace("\u200c", " ")
        )

        detected_marker: str | None = None

        for marker in VISIBLE_BLOCK_MARKERS:
            normalized_marker = (
                marker
                .lower()
                .replace("\u200c", " ")
            )

            if (
                normalized_marker
                in normalized_visible_text
            ):
                detected_marker = marker
                break

        content_type = response.headers.get(
            "Content-Type",
            "",
        )

        return {
            "status_code": response.status_code,
            "final_url": response.url,
            "content_type": content_type,
            "response_bytes": len(
                response.content
            ),
            "response_characters": len(
                response.text
            ),
            "page_title": title,
            "ad_link_count": len(
                advertisement_links
            ),
            "detected_marker": detected_marker,
            "expect_search_results": (
                expect_search_results
            ),
            "redirect_count": len(
                response.history
            ),
            "server": response.headers.get(
                "Server",
                "",
            ),
        }

    def _log_diagnostics(
        self,
        diagnostics: dict[str, object],
    ) -> None:
        LOGGER.warning(
            (
                "Divar response diagnostics | "
                "status=%s | final_url=%s | "
                "content_type=%s | bytes=%s | "
                "title=%r | ad_links=%s | "
                "marker=%r | redirects=%s"
            ),
            diagnostics.get(
                "status_code"
            ),
            diagnostics.get(
                "final_url"
            ),
            diagnostics.get(
                "content_type"
            ),
            diagnostics.get(
                "response_bytes"
            ),
            diagnostics.get(
                "page_title"
            ),
            diagnostics.get(
                "ad_link_count"
            ),
            diagnostics.get(
                "detected_marker"
            ),
            diagnostics.get(
                "redirect_count"
            ),
        )

    def _save_diagnostic_response(
        self,
        response: Response,
        diagnostics: dict[str, object],
        reason: str,
    ) -> str:
        try:
            self.diagnostics_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            timestamp = datetime.now(
                timezone.utc
            ).strftime(
                "%Y%m%dT%H%M%SZ"
            )

            base_name = (
                f"{timestamp}-"
                f"{response.status_code}-"
                f"{reason}"
            )

            html_path = (
                self.diagnostics_directory
                / f"{base_name}.html"
            )

            metadata_path = (
                self.diagnostics_directory
                / f"{base_name}.json"
            )

            # برای جلوگیری از بزرگ‌شدن State،
            # حداکثر یک میلیون کاراکتر ذخیره می‌شود.
            html_path.write_text(
                response.text[:1_000_000],
                encoding="utf-8",
            )

            metadata = dict(
                diagnostics
            )

            metadata["reason"] = reason
            metadata["saved_at_utc"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            metadata_path.write_text(
                json.dumps(
                    metadata,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            self._cleanup_old_diagnostics()

            LOGGER.warning(
                "Divar diagnostic files saved: %s",
                metadata_path,
            )

            return str(
                metadata_path
            )

        except OSError as exc:
            LOGGER.warning(
                "Could not save Divar diagnostics: %s",
                exc,
            )

            return "diagnostic_save_failed"

    def _cleanup_old_diagnostics(
        self,
    ) -> None:
        try:
            files = sorted(
                self.diagnostics_directory.glob(
                    "*"
                ),
                key=lambda path: (
                    path.stat().st_mtime
                ),
                reverse=True,
            )

        except OSError:
            return

        # سه پاسخ آخر، هرکدام شامل HTML و JSON.
        for old_file in files[6:]:
            try:
                old_file.unlink(
                    missing_ok=True
                )

            except OSError:
                continue

    def _load_cookies(self) -> None:
        if not self.cookies_path.exists():
            return

        try:
            raw_data = (
                self.cookies_path.read_text(
                    encoding="utf-8",
                )
            )

            cookie_items = json.loads(
                raw_data
            )

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return

        if not isinstance(
            cookie_items,
            list,
        ):
            return

        for item in cookie_items:
            if not isinstance(
                item,
                dict,
            ):
                continue

            name = str(
                item.get(
                    "name",
                    "",
                )
            ).strip()

            value = str(
                item.get(
                    "value",
                    "",
                )
            )

            if not name:
                continue

            cookie_arguments: dict[
                str,
                object,
            ] = {
                "name": name,
                "value": value,
                "path": str(
                    item.get(
                        "path",
                        "/",
                    )
                ),
                "secure": bool(
                    item.get(
                        "secure",
                        False,
                    )
                ),
                "expires": item.get(
                    "expires"
                ),
            }

            domain = str(
                item.get(
                    "domain",
                    "",
                )
            ).strip()

            if domain:
                cookie_arguments[
                    "domain"
                ] = domain

            cookie = (
                requests.cookies
                .create_cookie(
                    **cookie_arguments
                )
            )

            self.session.cookies.set_cookie(
                cookie
            )

    def _save_cookies(self) -> None:
        self.cookies_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        cookie_items = []

        for cookie in self.session.cookies:
            cookie_items.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                }
            )

        temporary_path = (
            self.cookies_path.with_suffix(
                self.cookies_path.suffix
                + ".tmp"
            )
        )

        try:
            temporary_path.write_text(
                json.dumps(
                    cookie_items,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            temporary_path.replace(
                self.cookies_path
            )

        except OSError:
            temporary_path.unlink(
                missing_ok=True
            )
