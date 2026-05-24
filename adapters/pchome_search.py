"""PChome 24h search-based discovery adapter.

Hits PChome's public search API:
    https://ecshweb.pchome.com.tw/search/v4.3/all/results?q=<query>&page=<n>

Returns pure JSON (no JSONP wrapping). Each result exposes Id / Name / Price /
OriginPrice but NOT stock — the monitor doesn't need stock because the value
prop here is catching newly-appeared cheap listings; user can verify stock by
clicking through.

We paginate through every page reported by `TotalPage` so the result set is
deterministic and we don't miss cheap listings buried on later pages.

Returns {status, detail, listings} where each listing is
  {id, title, price, url}.
"""
from __future__ import annotations

import httpx

from adapters._keyword import title_matches

SEARCH_ENDPOINT = "https://ecshweb.pchome.com.tw/search/v4.3/all/results"
PROD_URL = "https://24h.pchome.com.tw/prod/{id}"
MAX_PAGES = 20  # defensive cap; "MacBook Pro M5" today is 5 pages

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def check(item: dict) -> dict:
    """Search PChome and return listings matching the keyword filter.

    item fields:
      query:    required, search string sent to PChome
      keywords: optional list, ALL must appear (case-insensitive) in title
    """
    query = item.get("query")
    if not query:
        return {"status": "error", "detail": "缺少 query 欄位", "listings": []}
    keywords = item.get("keywords") or []

    listings: list[dict] = []
    try:
        page = 1
        total_pages = 1
        while page <= min(total_pages, MAX_PAGES):
            r = httpx.get(
                SEARCH_ENDPOINT,
                params={"q": query, "page": page},
                headers=HEADERS,
                timeout=30.0,
                follow_redirects=True,
            )
            r.raise_for_status()
            data = r.json()
            total_pages = int(data.get("TotalPage") or 1)
            for prod in data.get("Prods") or []:
                pid = prod.get("Id")
                name = prod.get("Name") or ""
                if not pid or not title_matches(name, keywords):
                    continue
                price = prod.get("Price")
                listings.append({
                    "id": pid,
                    "title": name,
                    "price": int(price) if isinstance(price, (int, float)) else None,
                    "url": PROD_URL.format(id=pid),
                })
            page += 1
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "listings": []}

    return {
        "status": "ok",
        "detail": f"PChome 「{query}」匹配 {len(listings)} 件 (掃過 {min(total_pages, MAX_PAGES)} 頁)",
        "listings": listings,
    }
