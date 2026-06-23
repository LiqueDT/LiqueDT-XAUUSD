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


def load_market_resilient() -> dict:
    try:
        return server.load_market()
    except Exception:
        return load_market_yfinance()


def load_market_yfinance() -> dict:
    import yfinance as yf

    items: list[dict] = []
    weighted_score = 0.0
    total_weight = 0.0
    for series in server.MARKET_SERIES:
        try:
            instrument = yf.Ticker(str(series["ticker"]))
            intraday = instrument.history(period="5d", interval="5m", auto_adjust=False, timeout=12)
            daily = instrument.history(period="5d", interval="1d", auto_adjust=False, timeout=12)
            intraday_close = intraday["Close"].dropna()
            daily_close = daily["Close"].dropna()
            if intraday_close.empty:
                continue
            price = float(intraday_close.iloc[-1])
            previous = float(daily_close.iloc[-2]) if len(daily_close) >= 2 else float(intraday_close.iloc[0])
            if not previous:
                continue
            change_percent = (price - previous) / previous * 100
            normalized_move = max(-1.0, min(1.0, change_percent / 0.75))
            gold_score = normalized_move * float(series["relation"])
            weight = float(series["weight"])
            weighted_score += gold_score * weight
            total_weight += weight
            items.append({
                "id": series["id"],
                "name": series["name"],
                "ticker": series["ticker"],
                "price": round(price, 5),
                "change_percent": round(change_percent, 3),
                "gold_score": round(gold_score, 3),
                "currency": "USD",
            })
        except Exception:
            continue

    if len(items) < 3 or not total_weight:
        raise ValueError("Insufficient fallback cross-market data")
    score = max(-1.0, min(1.0, weighted_score / total_weight))
    title = (
        "Cross-market context leans bullish" if score >= 0.18
        else "Cross-market context leans bearish" if score <= -0.18
        else "Cross-market context is balanced"
    )
    strongest = sorted(items, key=lambda item: abs(item["gold_score"]), reverse=True)[:3]
    summary = "Weighted market movement: " + ", ".join(
        f'{item["id"]} {"supports" if item["gold_score"] > .1 else "pressures" if item["gold_score"] < -.1 else "is neutral for"} gold'
        for item in strongest
    ) + ". Oil remains deliberately low-weight because its relationship with gold is indirect."
    return {
        "ok": True,
        "source": "Yahoo Finance via resilient market client",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "pulse": {"score": round(score, 3), "sample_size": len(items), "title": title, "summary": summary},
    }


def snapshot(name: str, loader, previous: dict | None = None) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        payload = loader()
        data_at = payload.get("updated_at") or generated_at
        return {
            **payload,
            "stale": False,
            "snapshot_status": "fresh",
            "snapshot_generated_at": generated_at,
            "snapshot_refreshed_at": generated_at,
            "snapshot_data_at": data_at,
        }
    except Exception as exc:  # The site should still deploy and show an honest red status.
        if previous and previous.get("ok"):
            previous_data_at = (
                previous.get("snapshot_data_at")
                or previous.get("updated_at")
                or previous.get("snapshot_refreshed_at")
                or previous.get("snapshot_generated_at")
            )
            previous_refreshed_at = previous.get("snapshot_refreshed_at") or previous.get("snapshot_generated_at")
            return {
                **previous,
                "stale": True,
                "warning": f"Refresh failed ({type(exc).__name__}); showing last successful snapshot",
                "snapshot_status": "stale_previous",
                "snapshot_generated_at": generated_at,
                "snapshot_attempted_at": generated_at,
                "snapshot_refreshed_at": previous_refreshed_at,
                "snapshot_data_at": previous_data_at,
                "snapshot_error": type(exc).__name__,
            }
        return {
            "ok": False,
            "stale": False,
            "source": name,
            "error": type(exc).__name__,
            "snapshot_status": "failed",
            "snapshot_generated_at": generated_at,
            "snapshot_attempted_at": generated_at,
        }


def read_previous(output: Path, name: str) -> dict | None:
    path = output / f"{name}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "data"))
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    feeds = {
        "market": snapshot("market", load_market_resilient, read_previous(output, "market")),
        "news": snapshot("news", server.load_news, read_previous(output, "news")),
        "calendar": snapshot("calendar", server.load_calendar, read_previous(output, "calendar")),
    }
    for name, payload in feeds.items():
        (output / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
