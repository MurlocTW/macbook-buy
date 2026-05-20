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
from adapters import apple, apple_refurb, pchome, studioa

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.yaml"
STATE_FILE = ROOT / "state.json"

# Continuous-error threshold before sending a warning notification.
ERROR_WARN_THRESHOLD = 3

# State-key prefix for Apple refurb listings (one key per part number seen).
REFURB_PREFIX = "apple_refurb:"

ADAPTERS = {
    "apple": apple.check,
    "pchome": pchome.check,
    "studioa": studioa.check,
    # "momo": momo.check,
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

    # Refurb items watch a whole category, not one product — handled separately.
    refurb_items = [p for p in products if p.get("platform") == "apple_refurb"]
    normal_items = [p for p in products if p.get("platform") != "apple_refurb"]

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

    # Refurb categories: push on any newly-appeared listing under max_price.
    for item in refurb_items:
        _handle_refurb(item, state, new_state)

    save_state(new_state)
    return 0


def _handle_refurb(item: dict, state: dict, new_state: dict) -> None:
    """Scan a refurb category. Notify once per newly-listed part within budget.

    State holds one key per part number ever seen (`apple_refurb:<part>`).
    A part is "new" when its key is absent from the previous state. To avoid
    spamming every existing listing on the very first run, we only notify once
    the category has been seeded at least once.
    """
    name = item.get("name") or "Apple 整修品"
    max_price = int(item.get("max_price", 100000))

    try:
        result = apple_refurb.check(item)
    except Exception as e:
        traceback.print_exc()
        result = {"status": "error", "detail": f"{type(e).__name__}: {e}", "listings": []}

    # On error keep prior refurb state intact — wiping it would re-notify
    # every listing as "new" once the fetch recovers.
    if result["status"] == "error":
        print(f"[error] {name} :: {result['detail']}")
        for k, v in state.items():
            if k.startswith(REFURB_PREFIX):
                new_state[k] = v
        return

    seeded = any(k.startswith(REFURB_PREFIX) for k in state)
    listings = result["listings"]
    print(f"[refurb] {name} :: {result['detail']} (門檻 NT${max_price:,})")

    for listing in listings:
        key = REFURB_PREFIX + listing["part"]
        is_new = key not in state
        price = listing["price"]
        new_state[key] = {
            "status": "listed",
            "title": listing["title"],
            "price": price,
            "url": listing["url"],
        }
        within = isinstance(price, int) and price <= max_price
        price_str = f"NT${price:,}" if isinstance(price, int) else "?"
        mark = "🆕" if is_new else "  "
        print(f"  {mark} {listing['title']} ({price_str})")

        if is_new and seeded and within:
            try:
                notify.send(notify.refurb_message(
                    listing["title"], item.get("note"), price, listing["url"], max_price,
                ))
            except Exception as e:
                print(f"[notify-error] {e}")

    if not seeded:
        print(f"[refurb] 首次執行,建立 {len(listings)} 筆 baseline,不發送通知")


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
