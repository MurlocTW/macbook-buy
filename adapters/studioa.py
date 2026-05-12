"""Studio A availability + price adapter.

Studio A's product page is a SPA, but the SSR HTML embeds the full product
state as a chained URL-encoded -> base64 -> URL-encoded -> JSON payload
sitting right after the API endpoint name `web-product-shelf-detail`.

We pull that blob, decode the chain, and read:
  data.productShelfSkuDetails[]:
    productSkuCode (e.g. "000-00002170-0001"),
    inventoryQuantity (int, 0 = out of stock),
    price, originalPrice,
    attributeValues: [{attributeName: '顏色', attributeValue: '太空黑' / '銀色'}]

If `color` is given in yaml, we only consider that SKU; otherwise any SKU with
inventoryQuantity > 0 counts as in_stock. Price returned is the matching SKU's
price (or, if no filter, the lowest in_stock SKU's price, falling back to the
first SKU's price).
"""
from __future__ import annotations

import base64
import json
import re
import urllib.parse

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

# Anchor on the API endpoint name; the encoded payload immediately follows.
_BLOB_RE = re.compile(r"web-product-shelf-detail.{0,40}?([A-Za-z0-9+/=%]{400,})", re.DOTALL)


def check(item: dict) -> dict:
    """Check one Studio A product. Returns {status, detail, url, price}.

    item fields:
      url:   required, e.g. "https://www.studioa.com.tw/products/mwb3ki"
      color: optional, "太空黑" or "銀色" — restrict to one variant
    """
    url = item["url"]
    color_filter = item.get("color")

    try:
        r = httpx.get(url, headers=HEADERS, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        skus = _extract_skus(r.text)
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "url": url, "price": None}

    if not skus:
        return {"status": "error", "detail": "找不到 SKU 資料 (頁面結構可能改了)", "url": url, "price": None}

    # Optional color filter
    if color_filter:
        skus = [s for s in skus if _sku_color(s) == color_filter]
        if not skus:
            return {"status": "error", "detail": f"找不到顏色={color_filter} 的 SKU", "url": url, "price": None}

    in_stock = [s for s in skus if int(s.get("inventoryQuantity") or 0) > 0]

    if in_stock:
        # Use the cheapest in-stock SKU's price.
        chosen = min(in_stock, key=lambda s: int(s.get("price") or 10**9))
        price = int(chosen.get("price") or 0) or None
        detail_parts = [f"{_sku_color(s) or s.get('productSkuCode')}: {int(s.get('inventoryQuantity'))} 台" for s in in_stock]
        return {
            "status": "in_stock",
            "detail": "Studio A 現貨 " + ", ".join(detail_parts),
            "url": url,
            "price": price,
        }

    # All out of stock — still surface a representative price.
    price = int(skus[0].get("price") or 0) or None
    return {
        "status": "out_of_stock",
        "detail": "Studio A 全色缺貨",
        "url": url,
        "price": price,
    }


def _extract_skus(html: str) -> list[dict]:
    m = _BLOB_RE.search(html)
    if not m:
        return []
    raw = m.group(1)
    try:
        s1 = urllib.parse.unquote(raw)
        s2 = base64.b64decode(s1).decode("utf-8")
        s3 = urllib.parse.unquote(s2)
        obj = json.loads(s3)
    except (ValueError, base64.binascii.Error, UnicodeDecodeError) as e:
        raise ValueError(f"failed to decode embedded blob: {e}")
    return ((obj.get("data") or {}).get("productShelfSkuDetails") or [])


def _sku_color(sku: dict) -> str | None:
    for attr in sku.get("attributeValues") or []:
        if attr.get("attributeName") == "顏色":
            return attr.get("attributeValue")
    return None
