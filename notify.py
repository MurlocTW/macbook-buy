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


def listing_message(
    header: str,
    title: str,
    note: str | None,
    price: int | None,
    url: str | None,
    threshold: int | None = None,
    discount: int | None = None,
    prev_price: int | None = None,
) -> str:
    """🆕 new-listing OR 📉 price-drop message.

    When `prev_price` is provided and higher than `price`, the message is
    framed as a price drop (📉) with a "↓ 從 NT$old 省 NT$diff" line.
    Otherwise it's a new-listing message (🆕).
    """
    is_drop = (
        isinstance(prev_price, int)
        and isinstance(price, int)
        and prev_price > price
    )
    emoji = "📉" if is_drop else "🆕"
    parts = [f"{emoji} <b>{html.escape(header)}</b>", "", html.escape(title)]
    if isinstance(price, int):
        line = f"<b>NT${price:,}</b>"
        if is_drop:
            diff = prev_price - price  # type: ignore[operator]
            line += f"  ↓ 從 NT${prev_price:,} 省 NT${diff:,}"
        if isinstance(discount, int) and discount > 0:
            line += f"  💸 比 Apple 便宜 NT${discount:,}"
        elif not is_drop and isinstance(threshold, int):
            line += f"  ✅ 在 NT${threshold:,} 門檻內"
        parts.append(line)
    if note:
        parts.append(html.escape(note))
    if url:
        parts.append(f"\n👉 {html.escape(url)}")
    return "\n".join(parts)


def refurb_message(
    title: str,
    note: str | None,
    price: int | None,
    url: str | None,
    threshold: int | None = None,
) -> str:
    """Back-compat shim. Prefer listing_message() for new call sites."""
    return listing_message("整修品上架!", title, note, price, url, threshold)


def warning_message(name: str, detail: str) -> str:
    return (
        f"⚠️ <b>監控警告</b>\n\n"
        f"{html.escape(name)} 連續多次抓取失敗\n"
        f"<i>{html.escape(detail)}</i>"
    )
