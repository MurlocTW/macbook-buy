"""Main monitor loop: read products.yaml, run adapters, diff against state.json,
   send Telegram on out_of_stock -> in_stock transitions."""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# Ensure Chinese output works on Windows / PowerShell (cp1252 default).
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

import yaml

import notify
from adapters import apple, apple_refurb, pchome, pchome_search, studioa, studioa_search

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.yaml"
STATE_FILE = ROOT / "state.json"

# Continuous-error threshold before sending a warning notification.
ERROR_WARN_THRESHOLD = 3

ADAPTERS = {
    "apple": apple.check,
    "pchome": pchome.check,
    "studioa": studioa.check,
    # "momo": momo.check,
}

# Search/discovery adapters: scan a search or category endpoint and notify on
# newly-appeared listings matching keywords + within max_price. Each entry maps
# `platform` -> (check_fn, state_key_prefix, channel_label_for_notification).
SCANS = {
    "apple_refurb":   (apple_refurb.check,   "apple_refurb:",   "整修品上架!"),
    "pchome_search":  (pchome_search.check,  "pchome_search:",  "PChome 新上架!"),
    "studioa_search": (studioa_search.check, "studioa_search:", "Studio A 新上架!"),
}


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def product_key(item: dict) -> str:
    """Stable identifier per product across runs."""
    plat = item["platform"]
    if plat == "apple":
        return f"apple:{item['part_number']}:{item.get('location','100')}"
    if plat == "pchome":
        return f"pchome:{item['product_id']}"
    if plat == "studioa":
        return f"studioa:{item['url']}:{item.get('color','')}"
    if plat == "momo":
        return f"momo:{item['url']}"
    return f"{plat}:{item.get('name','?')}"


def main() -> int:
    cfg = yaml.safe_load(PRODUCTS_FILE.read_text(encoding="utf-8")) or {}
    products = cfg.get("products") or []
    state = load_state()

    # Scan items watch a whole search/category, not one product — handled separately.
    scan_items = [p for p in products if p.get("platform") in SCANS]
    normal_items = [p for p in products if p.get("platform") not in SCANS]

    # Pass 1: run every adapter; collect Apple prices as baselines.
    runs = []  # list of (item, key, result)
    apple_prices: dict[str, int] = {}
    for item in normal_items:
        name = item.get("name") or item.get("part_number") or "?"
        plat = item.get("platform")
        key = product_key(item)
        check = ADAPTERS.get(plat)
        if not check:
            print(f"[skip] {name}: no adapter for platform={plat}")
            runs.append((item, key, None))
            continue
        try:
            result = check(item)
        except Exception as e:
            traceback.print_exc()
            result = {"status": "error", "detail": f"{type(e).__name__}: {e}", "url": None, "price": None}
        runs.append((item, key, result))
        if plat == "apple" and isinstance(result.get("price"), int):
            apple_prices[item["part_number"]] = result["price"]

    # Pass 2: compute baseline delta, log, write state, send notifications.
    new_state: dict = {}
    notify_warnings: list[tuple[str, str]] = []

    for item, key, result in runs:
        name = item.get("name") or item.get("part_number") or "?"
        prev = state.get(key, {})
        prev_status = prev.get("status", "unknown")
        prev_err_count = int(prev.get("error_count", 0))

        if result is None:  # unsupported adapter
            new_state[key] = prev
            continue

        status = result["status"]
        detail = result.get("detail", "")
        url = result.get("url")
        price = result.get("price")

        # Discount vs Apple baseline (only for non-Apple, with linked baseline_part).
        discount = _discount_vs_apple(item, price, apple_prices)
        if discount is not None and discount > 0:
            detail = f"{detail} | 比 Apple 便宜 NT${discount:,}"

        err_count = prev_err_count + 1 if status == "error" else 0
        price_str = f"NT${price:,}" if isinstance(price, int) else "?"
        print(f"[{status}] {name} ({price_str}) :: {detail}")

        # Eligible to notify = in_stock AND cheaper than Apple baseline.
        # Push only on the false->true transition so we don't spam every 20 min.
        eligible = (
            status == "in_stock"
            and isinstance(discount, int)
            and discount > 0
        )
        prev_eligible = bool(prev.get("eligible", False))
        if eligible and not prev_eligible:
            try:
                notify.send(notify.restock_message(name, item.get("note"), detail, url, price, discount))
            except Exception as e:
                print(f"[notify-error] {e}")

        if err_count == ERROR_WARN_THRESHOLD:
            notify_warnings.append((name, detail))

        new_state[key] = {
            "status": status,
            "detail": detail,
            "price": price,
            "discount_vs_apple": discount,
            "eligible": eligible,
            "error_count": err_count,
        }

    for name, detail in notify_warnings:
        try:
            notify.send(notify.warning_message(name, detail))
        except Exception as e:
            print(f"[notify-error] {e}")

    # Search/category scans: push on any newly-appeared listing under max_price.
    for item in scan_items:
        scan_fn, key_prefix, header = SCANS[item["platform"]]
        _handle_listing_scan(
            item, state, new_state,
            scan_fn=scan_fn, key_prefix=key_prefix, header=header,
            apple_prices=apple_prices,
        )

    save_state(new_state)
    return 0


def _handle_listing_scan(
    item: dict,
    state: dict,
    new_state: dict,
    *,
    scan_fn,
    key_prefix: str,
    header: str,
    apple_prices: dict[str, int],
) -> None:
    """Generic handler for search/category scan adapters.

    Each scan returns {status, detail, listings: [{id, title, price, url, ...}]}.
    State holds one key per id ever seen (`<key_prefix><id>`); a listing is
    "new" iff its key is absent from the previous state. First-ever run for a
    given prefix seeds silently so we don't blast every existing listing.

    On adapter error we carry the previous keys forward unchanged — wiping
    them would treat every listing as new once the fetch recovers and spam
    notifications.
    """
    name = item.get("name") or item.get("platform") or "scan"
    max_price = int(item.get("max_price", 100000))
    baseline_part = item.get("baseline_part")
    baseline_price = apple_prices.get(baseline_part) if baseline_part else None

    try:
        result = scan_fn(item)
    except Exception as e:
        traceback.print_exc()
        result = {"status": "error", "detail": f"{type(e).__name__}: {e}", "listings": []}

    if result["status"] == "error":
        print(f"[error] {name} :: {result['detail']}")
        for k, v in state.items():
            if k.startswith(key_prefix):
                new_state[k] = v
        return

    seeded = any(k.startswith(key_prefix) for k in state)
    listings = result["listings"]
    print(f"[{item['platform']}] {name} :: {result['detail']} (門檻 NT${max_price:,})")

    for listing in listings:
        key = key_prefix + listing["id"]
        is_new = key not in state
        price = listing["price"]
        discount = (baseline_price - price
                    if isinstance(baseline_price, int) and isinstance(price, int)
                    else None)
        new_state[key] = {
            "status": "listed",
            "title": listing["title"],
            "price": price,
            "url": listing["url"],
        }
        within = isinstance(price, int) and price <= max_price
        price_str = f"NT${price:,}" if isinstance(price, int) else "?"
        mark = "🆕" if is_new else "  "
        extra = f" | 比 Apple 便宜 NT${discount:,}" if isinstance(discount, int) and discount > 0 else ""
        print(f"  {mark} {listing['title'][:70]} ({price_str}){extra}")

        if is_new and seeded and within:
            try:
                notify.send(notify.listing_message(
                    header, listing["title"], item.get("note"),
                    price, listing["url"], max_price,
                    discount if isinstance(discount, int) and discount > 0 else None,
                ))
            except Exception as e:
                print(f"[notify-error] {e}")

    if not seeded:
        print(f"[{item['platform']}] 首次執行,建立 {len(listings)} 筆 baseline,不發送通知")


def _discount_vs_apple(item: dict, price, apple_prices: dict[str, int]) -> int | None:
    """Return (apple_baseline - listing_price). Positive = listing is cheaper.

    Returns None when not applicable: Apple platform itself, no baseline_part,
    no price, or baseline part not currently being monitored / errored.
    """
    if item.get("platform") == "apple":
        return None
    baseline_part = item.get("baseline_part")
    if not baseline_part or not isinstance(price, int):
        return None
    baseline_price = apple_prices.get(baseline_part)
    if not isinstance(baseline_price, int):
        return None
    return baseline_price - price


if __name__ == "__main__":
    sys.exit(main())
