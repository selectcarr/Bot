import requests
import os


class TelegramSenderError(Exception):
    pass


class TelegramSender:
    def __init__(self, token=None, chat_id=None):
        # اگر از app.py مقدار اومد استفاده کن
        self.token = token or os.getenv("DIVAR_TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("DIVAR_TELEGRAM_CHAT_ID")

        if not self.token or not self.chat_id:
            raise TelegramSenderError("Telegram config missing")

    def send(self, text: str):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"

        resp = requests.post(url, json={
            "chat_id": self.chat_id,
            "text": text
        })

        if resp.status_code != 200:
            raise TelegramSenderError(f"Telegram failed: {resp.text}")

    def send_test(self):
        self.send("✅ تست ربات دیوار انجام شد")