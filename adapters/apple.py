"""Apple TW availability + price adapter.

Two endpoints:

1. Pickup / availability:
     https://www.apple.com/tw/shop/retail/pickup-message?pl=true&parts.0=<part>&location=<postal>
   Response: body.stores[].partsAvailability.<part>.pickupDisplay
   Values: "available" | "unavailable" | "ineligible"

2. Price (no public JSON endpoint; scrape buy page):
     https://www.apple.com/tw/shop/product/<part>  -> 301 -> canonical buy URL with
     embedded JSON containing `"partNumber":"<part>","price":{"fullPrice":<number>`

We treat pickupDisplay == "available" as in_stock. If `stores` filter is given,
only those storeNumbers count.
"""
from __future__ import annotations

import re

import httpx

PICKUP_ENDPOINT = "https://www.apple.com/tw/shop/retail/pickup-message"
PRODUCT_REDIRECT = "https://www.apple.com/tw/shop/product/{part}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.apple.com/tw/shop/buy-mac/macbook-pro",
}


def check(item: dict) -> dict:
    """Check one product. Returns {status, detail, url, price}.

    item fields:
      part_number: required, e.g. "MDE34TA/A"
      location:    postal code, default "100" (Taipei)
      stores:      optional list of storeNumber filters, e.g. ["R713"]
    """
    part = item["part_number"]
    location = item.get("location", "100")
    target_stores = set(item.get("stores") or [])

    # Pickup availability
    try:
        r = httpx.get(
            PICKUP_ENDPOINT,
            params={"pl": "true", "parts.0": part, "location": location},
            headers=HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "detail": f"pickup: {type(e).__name__}: {e}", "url": None, "price": None}

    price = _fetch_price(part)

    stores = (data.get("body") or {}).get("stores") or []
    if not stores:
        return {"status": "out_of_stock", "detail": "no stores returned", "url": _buy_url(part), "price": price}

    available_at = []
    for s in stores:
        if target_stores and s.get("storeNumber") not in target_stores:
            continue
        avail = (s.get("partsAvailability") or {}).get(part) or {}
        if avail.get("pickupDisplay") == "available":
            available_at.append(s.get("storeName") or s.get("storeNumber"))

    if available_at:
        return {
            "status": "in_stock",
            "detail": "現貨可取: " + ", ".join(available_at[:5]),
            "url": _buy_url(part),
            "price": price,
        }
    return {
        "status": "out_of_stock",
        "detail": "全台 Apple Store 無現貨",
        "url": _buy_url(part),
        "price": price,
    }


_PRICE_RE = re.compile(r'"partNumber":"([A-Z0-9/]+)","price":\{"fullPrice":(\d+(?:\.\d+)?)')


def _fetch_price(part: str) -> int | None:
    """Scrape the buy page HTML for `fullPrice`. Returns NT$ as int, or None on failure.

    The page contains entries for every related part number (different color/spec);
    we pick the one matching `part` exactly.
    """
    try:
        r = httpx.get(
            PRODUCT_REDIRECT.format(part=part),
            headers=HEADERS,
            timeout=30.0,
            follow_redirects=True,
        )
        r.raise_for_status()
    except httpx.HTTPError:
        return None

    for m in _PRICE_RE.finditer(r.text):
        if m.group(1) == part:
            return int(float(m.group(2)))
    return None


def _buy_url(part: str) -> str:
    return f"https://www.apple.com/tw/shop/buy-mac/macbook-pro?product={part}"
