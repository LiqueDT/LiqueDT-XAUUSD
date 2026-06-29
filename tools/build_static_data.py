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
    histories: dict[str, list[float]] = {}
    for series in server.MARKET_SERIES:
        try:
            instrument = yf.Ticker(str(series["ticker"]))
            daily = instrument.history(period="6mo", interval="1d", auto_adjust=False, timeout=12)
            histories[str(series["ticker"])] = [float(value) for value in daily["Close"].dropna().tolist()]
        except Exception:
            continue
    gold_history = histories.get("GC=F", [])

    for series in server.MARKET_SERIES:
        try:
            instrument = yf.Ticker(str(series["ticker"]))
            intraday = instrument.history(period="5d", interval="5m", auto_adjust=False, timeout=12)
            intraday_close = intraday["Close"].dropna()
            daily_close = histories.get(str(series["ticker"]), [])
            if intraday_close.empty:
                continue
            price = float(intraday_close.iloc[-1])
            previous = float(daily_close[-2]) if len(daily_close) >= 2 else float(intraday_close.iloc[0])
            if not previous:
                continue
            change_percent = (price - previous) / previous * 100
            normalized_move = max(-1.0, min(1.0, change_percent / float(series.get("move_scale", 0.75))))
            if series["id"] == "XAUUSD":
                corr_20, corr_60 = 1.0, 1.0
            else:
                corr_20 = server.rolling_corr(gold_history, daily_close, 20)
                corr_60 = server.rolling_corr(gold_history, daily_close, 60)
            relation_used = server.effective_relation(series, corr_60)
            gold_score = normalized_move * relation_used
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
                "assumed_relation": float(series["relation"]),
                "effective_relation": round(relation_used, 3),
                "correlation_20": corr_20,
                "correlation_60": corr_60,
                "correlation_strength": server.correlation_strength(corr_60),
                "correlation_label": server.correlation_label(corr_60),
                "correlation_note": server.correlation_note(series, corr_20, corr_60),
                "relation_source": "rolling_60d_correlation" if corr_60 is not None and series["id"] != "XAUUSD" else "primary_or_macro_fallback",
                "data_proxy": series["id"] == "XAUUSDT",
                "proxy_note": "Backend uses XAUT-USD as the closest public proxy; chart tab remains BINANCE:XAUUSDT.P." if series["id"] == "XAUUSDT" else "",
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
    summary = "Correlation-aware XAUUSD movement: " + ", ".join(
        f'{item["id"]} {"supports" if item["gold_score"] > .1 else "pressures" if item["gold_score"] < -.1 else "is neutral for"} gold ({item.get("correlation_label", "correlation n/a")})'
        for item in strongest
    ) + ". The gauge is driven by each market move multiplied by its rolling XAUUSD correlation; weak regimes are muted."
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


def feed_status(name: str, payload: dict) -> dict:
    status = {
        "ok": bool(payload.get("ok")),
        "stale": bool(payload.get("stale")),
        "source": payload.get("source") or name,
        "snapshot_status": payload.get("snapshot_status") or ("stale" if payload.get("stale") else "fresh"),
        "snapshot_generated_at": payload.get("snapshot_generated_at"),
        "snapshot_refreshed_at": payload.get("snapshot_refreshed_at"),
        "snapshot_data_at": payload.get("snapshot_data_at") or payload.get("updated_at"),
        "warning": payload.get("warning"),
        "error": payload.get("error") or payload.get("snapshot_error"),
    }
    if isinstance(payload.get("items"), list):
        status["item_count"] = len(payload["items"])
        if payload["items"]:
            status["latest_item_at"] = payload["items"][0].get("published")
    if isinstance(payload.get("events"), list):
        status["event_count"] = len(payload["events"])
        if payload["events"]:
            status["next_event_at"] = payload["events"][0].get("time_utc")
    return status


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
    generated_at = datetime.now(timezone.utc).isoformat()
    status = {
        "ok": any(payload.get("ok") for payload in feeds.values()),
        "source": "GitHub Actions static snapshot builder",
        "updated_at": generated_at,
        "snapshot_generated_at": generated_at,
        "feeds": {name: feed_status(name, payload) for name, payload in feeds.items()},
    }
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
