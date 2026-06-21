"""Build GitHub Pages-safe market snapshots from the same normalized feed loaders."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def snapshot(name: str, loader) -> dict:
    try:
        payload = loader()
        return {**payload, "stale": False, "snapshot_generated_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:  # The site should still deploy and show an honest red status.
        return {
            "ok": False,
            "stale": False,
            "source": name,
            "error": type(exc).__name__,
            "snapshot_generated_at": datetime.now(timezone.utc).isoformat(),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "data"))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    feeds = {
        "market": snapshot("market", server.load_market),
        "news": snapshot("news", server.load_news),
        "calendar": snapshot("calendar", server.load_calendar),
    }
    for name, payload in feeds.items():
        (output / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
