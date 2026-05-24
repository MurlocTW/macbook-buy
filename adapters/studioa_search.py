"""Studio A category-based discovery adapter.

Studio A's category page server-renders a `serverApp-state` script that
contains a cached API response for `product-shelf-web/web-list`. We dig that
out (URL-decode → base64-decode → URL-decode → JSON) and read the structured
product list directly — no HTML regex scraping.

Each item exposes the slug (`productShelfId`), title, price, and stock flag,
so we get all the info we need from one HTTP call.

Returns {status, detail, listings} where each listing is
  {id, title, price, url}.
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse

import httpx

from adapters._keyword import title_matches

CATEGORY_URL = "https://www.studioa.com.tw/categories/{category}"
PROD_URL = "https://www.studioa.com.tw/products/{slug}"
WEB_LIST_KEY = "https://www.studioa.com.tw/backend/api/web/product-shelf-web/web-list"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

_STATE_RE = re.compile(
    r'<script[^>]+id="serverApp-state"[^>]*>(.+?)</script>', re.DOTALL,
)


def check(item: dict) -> dict:
    """Scan a Studio A category, optionally with a filter, and return listings.

    item fields:
      category: URL slug, default "macbook-pro"
      filter:   optional `?filter=` value (e.g. "m5" to limit to M5 models)
      keywords: optional list, ALL must appear (case-insensitive) in title
    """
    category = item.get("category", "macbook-pro")
    keywords = item.get("keywords") or []
    params = {"limit": 50}
    if item.get("filter"):
        params["filter"] = item["filter"]

    try:
        r = httpx.get(
            CATEGORY_URL.format(category=category),
            params=params, headers=HEADERS, timeout=30.0, follow_redirects=True,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "listings": []}

    items = _extract_items(r.text)
    if items is None:
        return {"status": "error", "detail": "serverApp-state 解析失敗", "listings": []}

    listings = []
    for it in items:
        name = it.get("name") or ""
        slug = it.get("productShelfId")
        if not slug or not title_matches(name, keywords):
            continue
        price = it.get("displayRetailPrice")
        listings.append({
            "id": slug,
            "title": name.strip(),
            "price": int(price) if isinstance(price, (int, float)) else None,
            "url": PROD_URL.format(slug=slug),
            "in_stock": bool(it.get("isHasInventory")),
        })

    return {
        "status": "ok",
        "detail": f"Studio A 「{category}」匹配 {len(listings)} 件",
        "listings": listings,
    }


def _extract_items(html: str) -> list[dict] | None:
    """Decode the serverApp-state blob and pull out the product-shelf list."""
    m = _STATE_RE.search(html)
    if not m:
        return None
    state_raw = (m.group(1)
                 .replace("&q;", '"').replace("&l;", "<")
                 .replace("&g;", ">").replace("&a;", "&"))
    try:
        state = json.loads(state_raw)
    except json.JSONDecodeError:
        return None
    encoded = state.get(WEB_LIST_KEY)
    if not encoded:
        return None
    try:
        decoded = urllib.parse.unquote(
            base64.b64decode(urllib.parse.unquote(encoded)).decode("utf-8")
        )
        api = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    return (api.get("data") or {}).get("items") or []
