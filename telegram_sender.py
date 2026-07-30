from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from divar_service.config.settings import Settings
from divar_service.models import DealCandidate


class TelegramSenderError(RuntimeError):
    """Raised when Telegram cannot validate or send a message."""


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
            )

        # پیش از ارسال، توکن اعتبارسنجی می‌شود.
        self._validate_bot_token()

        return self._send_text(message)

    def send_test_message(
        self,
    ) -> TelegramSendResult:
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
            )

        bot_info = self._validate_bot_token()

        username = bot_info.get("username")

        if isinstance(username, str) and username:
            print(
                "Telegram bot validated successfully: "
                f"@{username}"
            )
        else:
            print(
                "Telegram bot token validated successfully."
            )

        return self._send_text(message)

    def _validate_bot_token(
        self,
    ) -> dict[str, Any]:
        """
        Validate the token with Telegram's getMe method.
        """
        response_data = self._request(
            method_name="getMe",
            payload={},
        )

        result = response_data.get("result")

        if not isinstance(result, dict):
            raise TelegramSenderError(
                "Telegram getMe returned no bot information."
            )

        return result

    def _send_text(
        self,
        message: str,
    ) -> TelegramSendResult:
        chat_id = self._clean_chat_id()

        response_data = self._request(
            method_name="sendMessage",
            payload={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )

        result = response_data.get("result")

        message_id: int | None = None

        if isinstance(result, dict):
            raw_message_id = result.get("message_id")

            if isinstance(raw_message_id, int):
                message_id = raw_message_id

        return TelegramSendResult(
            sent=True,
            dry_run=False,
            message_id=message_id,
        )

    def _request(
        self,
        method_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        token = self._clean_token()

        api_url = (
            "https://api.telegram.org/"
            f"bot{token}/{method_name}"
        )

        try:
            response = requests.post(
                api_url,
                json=payload,
                timeout=(
                    self.settings
                    .request_timeout_seconds
                ),
            )

        except requests.Timeout as exc:
            raise TelegramSenderError(
                f"Telegram {method_name} timed out."
            ) from exc

        except requests.RequestException as exc:
            raise TelegramSenderError(
                f"Telegram {method_name} request failed: "
                f"{exc}"
            ) from exc

        try:
            response_data = response.json()

        except ValueError as exc:
            raise TelegramSenderError(
                "Telegram returned a non-JSON response. "
                f"HTTP status: {response.status_code}"
            ) from exc

        if not isinstance(response_data, dict):
            raise TelegramSenderError(
                "Telegram returned an invalid JSON response."
            )

        description = response_data.get(
            "description",
            "unknown_error",
        )

        if response.status_code == 404:
            raise TelegramSenderError(
                "Telegram returned HTTP 404. "
                "The bot token is empty, malformed, "
                "or invalid."
            )

        if response.status_code != 200:
            raise TelegramSenderError(
                f"Telegram returned HTTP "
                f"{response.status_code}: {description}"
            )

        if response_data.get("ok") is not True:
            raise TelegramSenderError(
                f"Telegram rejected {method_name}: "
                f"{description}"
            )

        return response_data

    def _clean_token(self) -> str:
        """
        Remove accidental spaces, quotes and a leading
        'bot' prefix from the stored token.
        """
        token = (
            self.settings
            .telegram_bot_token
            .strip()
            .strip('"')
            .strip("'")
        )

        if token.lower().startswith("bot"):
            token = token[3:].strip()

        if not token:
            raise TelegramSenderError(
                "Telegram bot token is missing."
            )

        if ":" not in token:
            raise TelegramSenderError(
                "Telegram bot token is malformed: "
                "the ':' separator is missing."
            )

        if any(
            character.isspace()
            for character in token
        ):
            raise TelegramSenderError(
                "Telegram bot token contains whitespace."
            )

        return token

    def _clean_chat_id(self) -> str:
        chat_id = (
            self.settings
            .telegram_chat_id
            .strip()
            .strip('"')
            .strip("'")
        )

        if not chat_id:
            raise TelegramSenderError(
                "Telegram chat ID is missing."
            )

        return chat_id


def format_deal_message(
    deal: DealCandidate,
) -> str:
    vehicle_parts = [
        deal.ad.brand.strip(),
        deal.ad.model.strip(),
    ]

    if deal.ad.trim.strip():
        vehicle_parts.append(
            deal.ad.trim.strip()
        )

    vehicle_name = " ".join(vehicle_parts)

    return (
        "🚗 دیل ویژه\n\n"
        "🚘 خودرو:\n"
        f"{vehicle_name}\n\n"
        "📅 مدل:\n"
        f"{deal.ad.year}\n\n"
        "💰 قیمت:\n"
        f"{format_money(deal.ad.price)} تومان\n\n"
        "📊 میانگین بازار:\n"
        f"{format_money(deal.market_average)} تومان\n\n"
        "📉 زیر قیمت:\n"
        f"{format_percent(deal.diff_percent)}٪\n\n"
        "🔗 لینک آگهی:\n"
        f"{deal.ad.url}"
    )


def format_money(value: int) -> str:
    return f"{value:,}"


def format_percent(value: float) -> str:
    return (
        f"{value:.2f}"
        .rstrip("0")
        .rstrip(".")
    )
