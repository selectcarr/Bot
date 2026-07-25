from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from divar_service.config.settings import Settings
from divar_service.models import DealCandidate


class TelegramSenderError(RuntimeError):
    """Raised when Telegram cannot deliver a message."""


@dataclass(frozen=True, slots=True)
class TelegramSendResult:
    sent: bool
    dry_run: bool
    message_id: int | None = None


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send_deal(
        self,
        deal: DealCandidate,
    ) -> TelegramSendResult:
        """
        Send one verified vehicle deal to Telegram.

        In dry-run mode, the message is only printed
        and nothing is sent.
        """
        message = format_deal_message(deal)

        if self.settings.dry_run:
            print(
                "\n--- DIVAR DRY RUN MESSAGE ---\n"
                f"{message}\n"
                "--- END MESSAGE ---\n"
            )

            return TelegramSendResult(
                sent=False,
                dry_run=True,
                message_id=None,
            )

        self._validate_credentials()

        api_url = (
            "https://api.telegram.org/"
            f"bot{self.settings.telegram_bot_token}/"
            "sendMessage"
        )

        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                api_url,
                json=payload,
                timeout=self.settings.request_timeout_seconds,
            )

        except requests.Timeout as exc:
            raise TelegramSenderError(
                "Telegram request timed out."
            ) from exc

        except requests.RequestException as exc:
            raise TelegramSenderError(
                "Telegram request failed."
            ) from exc

        try:
            response_data = response.json()
        except ValueError as exc:
            raise TelegramSenderError(
                "Telegram returned an invalid response."
            ) from exc

        if response.status_code != 200:
            description = _safe_description(
                response_data
            )

            raise TelegramSenderError(
                "Telegram returned HTTP status "
                f"{response.status_code}: {description}"
            )

        if not response_data.get("ok"):
            description = _safe_description(
                response_data
            )

            raise TelegramSenderError(
                f"Telegram rejected the message: {description}"
            )

        result = response_data.get("result")

        message_id: int | None = None

        if isinstance(result, dict):
            raw_message_id = result.get(
                "message_id"
            )

            if isinstance(raw_message_id, int):
                message_id = raw_message_id

        return TelegramSendResult(
            sent=True,
            dry_run=False,
            message_id=message_id,
        )

    def send_test_message(
        self,
    ) -> TelegramSendResult:
        """
        Send a simple connection test.

        This function will be used later during
        the manual GitHub Actions test.
        """
        message = (
            "✅ اتصال سیستم دیوار به تلگرام "
            "با موفقیت انجام شد."
        )

        if self.settings.dry_run:
            print(
                "\n--- TELEGRAM TEST DRY RUN ---\n"
                f"{message}\n"
                "--- END TEST ---\n"
            )

            return TelegramSendResult(
                sent=False,
                dry_run=True,
                message_id=None,
            )

        self._validate_credentials()

        api_url = (
            "https://api.telegram.org/"
            f"bot{self.settings.telegram_bot_token}/"
            "sendMessage"
        )

        payload: dict[str, Any] = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                api_url,
                json=payload,
                timeout=self.settings.request_timeout_seconds,
            )

            response_data = response.json()

        except requests.Timeout as exc:
            raise TelegramSenderError(
                "Telegram test request timed out."
            ) from exc

        except requests.RequestException as exc:
            raise TelegramSenderError(
                "Telegram test request failed."
            ) from exc

        except ValueError as exc:
            raise TelegramSenderError(
                "Telegram returned an invalid test response."
            ) from exc

        if (
            response.status_code != 200
            or not response_data.get("ok")
        ):
            description = _safe_description(
                response_data
            )

            raise TelegramSenderError(
                f"Telegram test failed: {description}"
            )

        result = response_data.get("result")
        message_id: int | None = None

        if isinstance(result, dict):
            raw_message_id = result.get(
                "message_id"
            )

            if isinstance(raw_message_id, int):
                message_id = raw_message_id

        return TelegramSendResult(
            sent=True,
            dry_run=False,
            message_id=message_id,
        )

    def _validate_credentials(self) -> None:
        if not self.settings.telegram_bot_token.strip():
            raise TelegramSenderError(
                "DIVAR_TELEGRAM_BOT_TOKEN is missing."
            )

        if not self.settings.telegram_chat_id.strip():
            raise TelegramSenderError(
                "DIVAR_TELEGRAM_CHAT_ID is missing."
            )


def format_deal_message(
    deal: DealCandidate,
) -> str:
    vehicle_name_parts = [
        deal.ad.brand.strip(),
        deal.ad.model.strip(),
    ]

    if deal.ad.trim.strip():
        vehicle_name_parts.append(
            deal.ad.trim.strip()
        )

    vehicle_name = " ".join(
        vehicle_name_parts
    )

    price = format_money(
        deal.ad.price
    )

    market_average = format_money(
        deal.market_average
    )

    percent = format_percent(
        deal.diff_percent
    )

    return (
        "🚗 دیل ویژه\n\n"
        "🚘 خودرو:\n"
        f"{vehicle_name}\n\n"
        "📅 مدل:\n"
        f"{deal.ad.year}\n\n"
        "💰 قیمت:\n"
        f"{price} تومان\n\n"
        "📊 میانگین بازار:\n"
        f"{market_average} تومان\n\n"
        "📉 زیر قیمت:\n"
        f"{percent}٪\n\n"
        "🔗 لینک آگهی:\n"
        f"{deal.ad.url}"
    )


def format_money(
    value: int,
) -> str:
    return f"{value:,}"


def format_percent(
    value: float,
) -> str:
    formatted = f"{value:.2f}"

    return formatted.rstrip(
        "0"
    ).rstrip(
        "."
    )


def _safe_description(
    response_data: object,
) -> str:
    if not isinstance(
        response_data,
        dict,
    ):
        return "unknown_error"

    description = response_data.get(
        "description"
    )

    if not isinstance(
        description,
        str,
    ):
        return "unknown_error"

    return description[:300]
