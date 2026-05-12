"""Telegram notification helper."""
from __future__ import annotations

import html
import os

import httpx


def send(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — skipping send")
        print(text)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    r = httpx.post(url, json=payload, timeout=15.0)
    r.raise_for_status()


def restock_message(
    name: str,
    note: str | None,
    detail: str,
    url: str | None,
    price: int | None = None,
    discount: int | None = None,
) -> str:
    name_e = html.escape(name)
    parts = [f"🔔 <b>補貨啦!</b>", "", name_e]
    if isinstance(price, int):
        price_line = f"<b>NT${price:,}</b>"
        if isinstance(discount, int) and discount > 0:
            price_line += f"  💸 <b>比 Apple 便宜 NT${discount:,}</b>"
        parts.append(price_line)
    if note:
        parts.append(html.escape(note))
    if detail:
        parts.append(f"<i>{html.escape(detail)}</i>")
    if url:
        # Show the URL as plain text so it's visible in Telegram (auto-linkified by client).
        parts.append(f"\n👉 {html.escape(url)}")
    return "\n".join(parts)


def warning_message(name: str, detail: str) -> str:
    return (
        f"⚠️ <b>監控警告</b>\n\n"
        f"{html.escape(name)} 連續多次抓取失敗\n"
        f"<i>{html.escape(detail)}</i>"
    )
