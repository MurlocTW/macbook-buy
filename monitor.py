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
from adapters import apple, pchome, studioa

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

    # Pass 1: run every adapter; collect Apple prices as baselines.
    runs = []  # list of (item, key, result)
    apple_prices: dict[str, int] = {}
    for item in products:
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

    save_state(new_state)
    return 0


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
