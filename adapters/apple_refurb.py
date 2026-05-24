"""Apple TW 整修品 (Certified Refurbished) adapter.

Unlike the other adapters this one watches a *whole category* instead of a
single known product — refurb listings appear and vanish unpredictably, so we
can't pin a part number in advance.

How it works:
  The refurb category page server-renders a JSON blob into
    window.REFURB_GRID_BOOTSTRAP = {...}
  whose `tiles` array is the COMPLETE refurbished-Mac catalogue. The React grid
  paginates/filters over it purely client-side (there is no product API), so the
  blob is guaranteed to hold every listing. We parse it and keep only tiles whose
  `filters.dimensions.refurbClearModel` matches the requested model.

Returns {status, detail, listings} where listings is a list of
  {id, title, price, url}.  `id` is the Apple part number (e.g. "G1FSNTA/A").
"""
from __future__ import annotations

import json
import re

import httpx

CATEGORY_URL = "https://www.apple.com/tw/shop/refurbished/mac/{category}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

_PRICE_DIGITS = re.compile(r"\d+")


def check(item: dict) -> dict:
    """Scan a refurb category. Returns {status, detail, listings}.

    item fields:
      category:     URL slug, default "macbook-pro"
      refurb_model: refurbClearModel value to keep, default "macbookpro"
    """
    category = item.get("category", "macbook-pro")
    model = item.get("refurb_model", "macbookpro")
    url = CATEGORY_URL.format(category=category)

    try:
        r = httpx.get(url, headers=HEADERS, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "listings": []}

    bootstrap = _extract_bootstrap(r.text)
    if bootstrap is None:
        return {"status": "error", "detail": "REFURB_GRID_BOOTSTRAP 解析失敗", "listings": []}

    listings = []
    for tile in bootstrap.get("tiles") or []:
        dims = (tile.get("filters") or {}).get("dimensions") or {}
        if dims.get("refurbClearModel") != model:
            continue
        part = tile.get("partNumber")
        if not part:
            continue
        path = tile.get("productDetailsUrl") or ""
        listings.append({
            "id": part,
            "title": (tile.get("title") or "").replace("\xa0", " ").strip(),
            "price": _parse_price(tile),
            "url": ("https://www.apple.com" + path) if path.startswith("/") else path,
        })

    return {
        "status": "ok",
        "detail": f"{model} 整修品共 {len(listings)} 件",
        "listings": listings,
    }


def _extract_bootstrap(html: str) -> dict | None:
    """Pull the `window.REFURB_GRID_BOOTSTRAP = {...}` object out of the page."""
    i = html.find("REFURB_GRID_BOOTSTRAP")
    if i < 0:
        return None
    start = html.find("{", i)
    if start < 0:
        return None
    depth = 0
    for j in range(start, len(html)):
        c = html[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:j + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _parse_price(tile: dict) -> int | None:
    """Refurb tile price -> NT$ int. Prefers raw_amount, falls back to amount."""
    cur = ((tile.get("price") or {}).get("currentPrice")) or {}
    raw = cur.get("raw_amount")
    if raw:
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            pass
    amount = cur.get("amount") or ""
    digits = "".join(_PRICE_DIGITS.findall(amount))
    return int(digits) if digits else None
