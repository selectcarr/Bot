import requests
import os


class TelegramSenderError(Exception):
    pass


def send_message(text: str):
    token = os.getenv("DIVAR_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("DIVAR_TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise TelegramSenderError("Telegram config missing")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    })

    if resp.status_code != 200:
        raise TelegramSenderError(f"Telegram failed: {resp.text}")


def send_test_message():
    send_message("✅ تست ربات دیوار انجام شد")
