"""PChome 24h availability + price adapter.

Endpoint (JSONP, callback param required):
  https://ecapi.pchome.com.tw/ecshop/prodapi/v2/prod/<ID>&fields=<csv>&_callback=cb

Response is wrapped in `try{cb({...});}catch(e){...}`. We strip the wrapper
to get a JSON object keyed by `<ID>-000` (a sub-SKU suffix).

Fields used:
  Qty:   integer, 0 = out of stock
  Price: { M: 原價/標價, P: PChome 直營標價, Low: 實際結帳價 (套折扣後), Prime: P 幣價 }
         我們取 Low 優先, 因為這才是頁面紅字顯示的、使用者實際會付的價。
  Name:  display name (for sanity / logs)
"""
from __future__ import annotations

import json
import re

import httpx

ENDPOINT = "https://ecapi.pchome.com.tw/ecshop/prodapi/v2/prod/{pid}&fields=Id,Name,Price,Qty,SaleStatus,ButtonType&_callback=cb"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://24h.pchome.com.tw/",
}

_JSONP_RE = re.compile(r"^\s*try\s*\{\s*cb\((.*)\)\s*;?\s*\}\s*catch", re.DOTALL)


def check(item: dict) -> dict:
    """Check one PChome listing. Returns {status, detail, url, price}.

    item fields:
      product_id: required, e.g. "DYAJEP-A900JCMV4"
    """
    pid = item["product_id"]

    try:
        r = httpx.get(ENDPOINT.format(pid=pid), headers=HEADERS, timeout=30.0, follow_redirects=True)
        r.raise_for_status()
        data = _parse_jsonp(r.text)
    except (httpx.HTTPError, ValueError) as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}", "url": _buy_url(pid), "price": None}

    # The JSONP payload is a dict keyed by "<pid>-000". Take the first (and usually only) entry.
    if not data:
        return {"status": "error", "detail": "empty response (product 下架?)", "url": _buy_url(pid), "price": None}

    record = next(iter(data.values()))
    qty = int(record.get("Qty", 0) or 0)
    price_obj = record.get("Price") or {}
    price = price_obj.get("Low") or price_obj.get("P") or price_obj.get("M")
    if price is not None:
        price = int(float(price))

    if qty > 0:
        return {
            "status": "in_stock",
            "detail": f"PChome 現貨 Qty={qty}",
            "url": _buy_url(pid),
            "price": price,
        }
    return {
        "status": "out_of_stock",
        "detail": "PChome 缺貨 (Qty=0)",
        "url": _buy_url(pid),
        "price": price,
    }


def _parse_jsonp(text: str) -> dict:
    m = _JSONP_RE.match(text)
    if not m:
        raise ValueError(f"unexpected JSONP shape, first 80 chars: {text[:80]!r}")
    return json.loads(m.group(1))


def _buy_url(pid: str) -> str:
    return f"https://24h.pchome.com.tw/prod/{pid}"
