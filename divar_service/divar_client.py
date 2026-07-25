from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

import requests
from requests import Response

from divar_service.config.settings import Settings


DEFAULT_SEARCH_URL = "https://divar.ir/s/tehran/car"

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
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


BLOCK_PAGE_MARKERS = (
    "captcha",
    "کپچا",
    "تعداد درخواست",
    "دسترسی شما محدود",
    "درخواست‌های غیرمجاز",
    "too many requests",
    "access denied",
)


class DivarClientError(RuntimeError):
    """Base error for Divar page requests."""


class DivarBlockedError(DivarClientError):
    """Raised when Divar returns a blocking response."""


class DivarClient:
    def __init__(self, settings: Settings) -> None:
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
        self.session.headers.update(DEFAULT_HEADERS)

        self.cookies_path = Path(
            settings.cookies_path
        )

        self._load_cookies()

    def fetch_search_page(
        self,
        page_number: int,
    ) -> str:
        """
        Fetch a Divar vehicle-search page.

        page_number=1:
            https://divar.ir/s/tehran/car

        page_number=2:
            https://divar.ir/s/tehran/car?page=1
        """
        if not 1 <= page_number <= self.settings.max_pages:
            raise ValueError(
                "page_number is outside the allowed range."
            )

        params: dict[str, Any] = {}

        if page_number > 1:
            params["page"] = page_number - 1

        response = self._get(
            url=self.search_url,
            params=params,
        )

        return response.text

    def fetch_ad_page(
        self,
        url: str,
    ) -> str:
        """
        Fetch one advertisement detail page.
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
        )

        return response.text

    def sleep_after_page(
        self,
        page_number: int,
    ) -> float:
        """
        Wait after receiving a search page.

        Page 1:
            2 to 6 seconds

        Pages 2 and 3:
            3 to 7 seconds
        """
        if page_number == 1:
            minimum = (
                self.settings.first_page_delay_min
            )
            maximum = (
                self.settings.first_page_delay_max
            )
        else:
            minimum = (
                self.settings.next_page_delay_min
            )
            maximum = (
                self.settings.next_page_delay_max
            )

        delay = random.uniform(
            minimum,
            maximum,
        )

        time.sleep(delay)

        return delay

    def close(self) -> None:
        self._save_cookies()
        self.session.close()

    def __enter__(self) -> DivarClient:
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
    ) -> Response:
        try:
            response = self.session.get(
                url,
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

        if response.status_code in {
            403,
            429,
        }:
            self._save_cookies()

            raise DivarBlockedError(
                "Divar returned a blocking status "
                f"code: {response.status_code}"
            )

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise DivarClientError(
                "Divar returned HTTP status "
                f"{response.status_code}."
            ) from exc

        content_type = response.headers.get(
            "Content-Type",
            "",
        ).lower()

        if (
            content_type
            and "text/html" not in content_type
        ):
            raise DivarClientError(
                "Divar response is not an HTML page."
            )

        page_text = response.text.lower()

        for marker in BLOCK_PAGE_MARKERS:
            if marker.lower() in page_text:
                self._save_cookies()

                raise DivarBlockedError(
                    "A blocking or verification page "
                    "was detected."
                )

        self._save_cookies()

        return response

    def _load_cookies(self) -> None:
        if not self.cookies_path.exists():
            return

        try:
            raw_data = self.cookies_path.read_text(
                encoding="utf-8",
            )

            cookie_items = json.loads(raw_data)

        except (
            OSError,
            json.JSONDecodeError,
        ):
            return

        if not isinstance(cookie_items, list):
            return

        for item in cookie_items:
            if not isinstance(item, dict):
                continue

            name = str(
                item.get("name", "")
            ).strip()

            value = str(
                item.get("value", "")
            )

            if not name:
                continue

            cookie = requests.cookies.create_cookie(
                name=name,
                value=value,
                domain=str(
                    item.get("domain", "")
                ),
                path=str(
                    item.get("path", "/")
                ),
                secure=bool(
                    item.get("secure", False)
                ),
                expires=item.get("expires"),
            )

            self.session.cookies.set_cookie(cookie)

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

        temporary_path = self.cookies_path.with_suffix(
            self.cookies_path.suffix + ".tmp"
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
            if temporary_path.exists():
                temporary_path.unlink(
                    missing_ok=True
                )
