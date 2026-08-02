from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from divar_service.config.settings import Settings
from divar_service.models import DealCandidate


class TelegramSenderError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TelegramSendResult:
    sent: bool
    dry_run: bool
    message_id: int | None = None


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.token = self._clean_token(settings.telegram_bot_token)
        self.chat_id = self._clean_chat_id(settings.telegram_chat_id)

    def send_deal(self, deal: DealCandidate) -> TelegramSendResult:
        message = format_deal_message(deal)

        if self._is_dry_run():
            print(
                "\n--- DIVAR DRY RUN MESSAGE ---\n"
                f"{message}\n"
                "--- END MESSAGE ---\n"
            )
            return TelegramSendResult(sent=False, dry_run=True)

        response_data = self._request(
            method_name="sendMessage",
            payload={
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )

        return TelegramSendResult(
            sent=True,
            dry_run=False,
            message_id=self._extract_message_id(response_data),
        )

    def send_test_message(self) -> TelegramSendResult:
        message = "✅ اتصال سیستم دیوار به تلگرام با موفقیت انجام شد."

        if self._is_dry_run():
            print(
                "\n--- TELEGRAM TEST DRY RUN ---\n"
                f"{message}\n"
                "--- END TEST ---\n"
            )
            return TelegramSendResult(sent=False, dry_run=True)

        response_data = self._request(
            method_name="sendMessage",
            payload={
                "chat_id": self.chat_id,
                "text": message,
                "disable_web_page_preview": True,
            },
        )

        return TelegramSendResult(
            sent=True,
            dry_run=False,
            message_id=self._extract_message_id(response_data),
        )

    def _request(self, method_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.token}/{method_name}"

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.settings.request_timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TelegramSenderError(
                f"Telegram {method_name} timed out."
            ) from exc
        except requests.RequestException as exc:
            raise TelegramSenderError(
                f"Telegram {method_name} request failed: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramSenderError(
                "Telegram returned a non-JSON response."
            ) from exc

        if response.status_code != 200:
            description = self._safe_description(data)
            raise TelegramSenderError(
                f"Telegram returned HTTP {response.status_code}: {description}"
            )

        if not isinstance(data, dict) or data.get("ok") is not True:
            description = self._safe_description(data)
            raise TelegramSenderError(
                f"Telegram rejected {method_name}: {description}"
            )

        return data

    def _extract_message_id(self, data: dict[str, Any]) -> int | None:
        result = data.get("result")
        if isinstance(result, dict):
            message_id = result.get("message_id")
            if isinstance(message_id, int):
                return message_id
        return None

    def _clean_token(self, token: str) -> str:
        token = (token or "").strip().strip('"').strip("'")
        if token.lower().startswith("bot"):
            token = token[3:].strip()

        if not token:
            raise TelegramSenderError("Telegram bot token is missing.")

        if ":" not in token:
            raise TelegramSenderError("Telegram bot token is malformed.")

        if any(ch.isspace() for ch in token):
            raise TelegramSenderError("Telegram bot token contains whitespace.")

        return token

    def _clean_chat_id(self, chat_id: str) -> str:
        chat_id = (chat_id or "").strip().strip('"').strip("'")
        if not chat_id:
            raise TelegramSenderError("Telegram chat ID is missing.")
        return chat_id

    def _is_dry_run(self) -> bool:
        return bool(getattr(self.settings, "dry_run", True))

    @staticmethod
    def _safe_description(data: object) -> str:
        if isinstance(data, dict):
            description = data.get("description")
            if isinstance(description, str) and description.strip():
                return description[:300]
        return "unknown_error"


def format_deal_message(deal: DealCandidate) -> str:
    parts = [deal.ad.brand.strip(), deal.ad.model.strip()]
    if deal.ad.trim.strip():
        parts.append(deal.ad.trim.strip())

    vehicle_name = " ".join(parts)

    return (
        "🚗 دیل ویژه\n\n"
        "🚘 خودرو:\n"
        f"{vehicle_name}\n\n"
        "📅 مدل:\n"
        f"{deal.ad.year}\n\n"
        "💰 قیمت:\n"
        f"{deal.ad.price:,} تومان\n\n"
        "📊 میانگین بازار:\n"
        f"{deal.market_average:,} تومان\n\n"
        "📉 زیر قیمت:\n"
        f"{deal.diff_percent:.2f}٪\n\n"
        "🔗 لینک آگهی:\n"
        f"{deal.ad.url}"
    )
