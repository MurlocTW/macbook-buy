"""列出 notifications.jsonl 最近 N 筆通知歷史。

    python history.py            # 最近 20 筆 (摘要)
    python history.py 50         # 最近 50 筆
    python history.py 5 --full   # 帶完整訊息內容
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOG = Path(__file__).parent / "notifications.jsonl"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv
    n = int(args[0]) if args else 20

    if not LOG.exists():
        print("(no history yet — notifications.jsonl 還沒被建立)")
        return 0

    lines = LOG.read_text(encoding="utf-8").splitlines()
    for line in lines[-n:]:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("ts", "?")
        status = e.get("status", "?")
        text = (e.get("text") or "").strip()
        first = text.splitlines()[0] if text else "(no text)"
        extra = ""
        if status == "error":
            extra = f"  err={e.get('error')}"
        elif status == "skipped":
            extra = f"  ({e.get('reason')})"
        print(f"{ts}  [{status:7}]  {first}{extra}")
        if full and text:
            for sub in text.splitlines()[1:]:
                print(f"                                    {sub}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
