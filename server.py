"""LiqueDT local gateway: static PWA + cached, normalized market-context feeds."""

from __future__ import annotations

import argparse
import calendar
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.8 fallback
    ZoneInfo = None  # type: ignore[assignment]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parent
NEWS_FEEDS = (
    ("https://www.fxstreet.com/rss/news", "FXStreet"),
    ("https://news.google.com/rss/search?q=%28gold%20OR%20XAUUSD%29%20%28Fed%20OR%20dollar%20OR%20Trump%20OR%20tariff%20OR%20geopolitical%29&hl=en-US&gl=US&ceid=US%3Aen", "Google News"),
)
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36 LiqueDT/1.7"
GOLD_TERMS = (
    "gold", "xau", "dollar", "usd", "fed", "fomc", "powell", "yield", "treasury",
    "inflation", "cpi", "pce", "jobs", "payroll", "geopolit", "war", "tariff", "oil",
    "trump", "white house", "sanction", "trade", "china", "middle east",
)

MARKET_SERIES = (
    {"id": "XAUUSD", "ticker": "GC=F", "name": "Gold futures proxy", "relation": 1.0, "weight": 0.22},
    {"id": "DXY", "ticker": "DX-Y.NYB", "name": "U.S. Dollar Index", "relation": -1.0, "weight": 0.36},
    {"id": "US10Y", "ticker": "^TNX", "name": "U.S. 10Y yield", "relation": -1.0, "weight": 0.32},
    {"id": "WTI", "ticker": "CL=F", "name": "WTI crude oil", "relation": 1.0, "weight": 0.06},
    {"id": "XAUUSDT", "ticker": "XAUT-USD", "name": "Tether Gold proxy", "relation": 1.0, "weight": 0.04},
)

BULLISH_PHRASES = {
    "rate cut": 2, "dovish": 2, "dollar weakens": 2, "dollar falls": 2,
    "dollar slides": 2, "lower yields": 2, "yields fall": 2, "safe haven": 1,
    "geopolitical tensions": 1, "conflict": 1, "uncertainty": 1, "recession": 1,
    "inflation fears": 1, "central bank buying": 2, "gold demand": 1,
}
BEARISH_PHRASES = {
    "rate hike": -2, "hawkish": -2, "dollar strengthens": -2, "dollar rises": -2,
    "dollar gains": -2, "higher yields": -2, "yields rise": -2, "risk-on": -1,
    "ceasefire": -1, "profit-taking": -1, "gold falls": -2, "gold retreats": -1,
}


@dataclass
class CacheEntry:
    value: dict[str, Any] | None = None
    fetched_at: float = 0.0


class FeedCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._master = threading.Lock()

    def get(self, key: str, ttl: int, loader: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        now = time.time()
        entry = self._entries.get(key)
        if entry and entry.value and now - entry.fetched_at < ttl:
            return {**entry.value, "stale": False}

        with self._master:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            entry = self._entries.get(key)
            now = time.time()
            if entry and entry.value and now - entry.fetched_at < ttl:
                return {**entry.value, "stale": False}
            try:
                value = loader()
                self._entries[key] = CacheEntry(value=value, fetched_at=now)
                return {**value, "stale": False}
            except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as exc:
                if entry and entry.value:
                    return {**entry.value, "stale": True, "warning": "Upstream refresh failed"}
                return {"ok": False, "stale": False, "error": type(exc).__name__}


CACHE = FeedCache()


def fetch_bytes(url: str, timeout: int = 8) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,application/rss+xml;q=0.9,*/*;q=0.5"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if getattr(response, "status", 200) != 200:
            raise urllib.error.URLError(f"Upstream returned {response.status}")
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Upstream payload exceeded 2 MB")
        return data


def text_of(node: ET.Element, name: str) -> str:
    child = node.find(name)
    return (child.text or "").strip() if child is not None else ""


def safe_external_url(value: str, fallback: str) -> str:
    try:
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return value
    except ValueError:
        pass
    return fallback


def headline_score(title: str) -> tuple[int, str, list[str]]:
    normalized = re.sub(r"\s+", " ", html.unescape(title).lower())
    score = sum(weight for phrase, weight in BULLISH_PHRASES.items() if phrase in normalized)
    score += sum(weight for phrase, weight in BEARISH_PHRASES.items() if phrase in normalized)
    impact = "bullish" if score > 0 else "bearish" if score < 0 else "mixed"
    factors: list[str] = []
    if any(term in normalized for term in ("dollar", "usd", "dxy")):
        factors.append("Dollar")
    if any(term in normalized for term in ("fed", "fomc", "rate", "yield", "treasury", "powell")):
        factors.append("Rates")
    if any(term in normalized for term in ("war", "risk", "geopolit", "conflict", "ceasefire")):
        factors.append("Risk")
    if any(term in normalized for term in ("inflation", "cpi", "pce", "oil")):
        factors.append("Inflation")
    return score, impact, factors


def load_news() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total_score = 0
    factors: dict[str, int] = {}
    seen: set[str] = set()

    for feed_url, default_source in NEWS_FEEDS:
        try:
            root = ET.fromstring(fetch_bytes(feed_url))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError):
            continue
        for node in root.findall(".//item"):
            title = re.sub(r"\s+", " ", html.unescape(text_of(node, "title"))).strip()
            title_key = title.casefold()
            if not title or title_key in seen or not any(term in title.lower() for term in GOLD_TERMS):
                continue
            seen.add(title_key)
            score, impact, item_factors = headline_score(title)
            total_score += score
            for factor in item_factors:
                factors[factor] = factors.get(factor, 0) + 1
            published_text = text_of(node, "pubDate")
            try:
                published = parsedate_to_datetime(published_text)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                published_iso = published.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                published_iso = None
            source = text_of(node, "source") or default_source
            items.append({
                "title": title,
                "url": safe_external_url(text_of(node, "link"), "https://www.fxstreet.com/markets/commodities/metals/gold"),
                "source": source,
                "published": published_iso,
                "impact": impact,
            })

    items.sort(key=lambda item: item["published"] or "", reverse=True)
    items = items[:18]

    if not items:
        raise ValueError("No relevant news items in upstream feed")

    normalized_score = max(-1.0, min(1.0, total_score / max(4, len(items) * 1.5)))
    if normalized_score >= 0.2:
        title = "Headlines lean supportive for gold"
        summary = "Recent coverage emphasizes language that can support gold, but price may already reflect the narrative."
    elif normalized_score <= -0.2:
        title = "Headlines lean restrictive for gold"
        summary = "Recent coverage emphasizes language that can pressure gold, though cross-market confirmation still matters."
    else:
        title = "The gold narrative is balanced"
        summary = "Recent headlines contain mixed gold-sensitive language with no clear aggregate lean."

    return {
        "ok": True,
        "source": "FXStreet + attributable public news",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "pulse": {
            "score": round(normalized_score, 3),
            "sample_size": len(items),
            "title": title,
            "summary": summary,
            "factors": [name for name, _ in sorted(factors.items(), key=lambda pair: pair[1], reverse=True)][:4],
        },
    }


def load_market() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0.0
    for series in MARKET_SERIES:
        ticker = urllib.parse.quote(str(series["ticker"]), safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=1d"
        try:
            payload = json.loads(fetch_bytes(url).decode("utf-8"))
            result = payload["chart"]["result"][0]
            meta = result["meta"]
            price = float(meta["regularMarketPrice"])
            previous = float(meta.get("chartPreviousClose") or meta.get("previousClose"))
            if not previous:
                continue
            change_percent = (price - previous) / previous * 100
            normalized_move = max(-1.0, min(1.0, change_percent / 0.75))
            gold_score = normalized_move * float(series["relation"])
            weight = float(series["weight"])
            weighted_score += gold_score * weight
            total_weight += weight
            items.append({
                "id": series["id"], "name": series["name"], "ticker": series["ticker"],
                "price": round(price, 5), "change_percent": round(change_percent, 3),
                "gold_score": round(gold_score, 3), "currency": meta.get("currency", "USD"),
            })
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError, urllib.error.URLError):
            continue

    if len(items) < 3 or not total_weight:
        raise ValueError("Insufficient live cross-market data")
    score = max(-1.0, min(1.0, weighted_score / total_weight))
    if score >= 0.18:
        title = "Cross-market context leans bullish"
    elif score <= -0.18:
        title = "Cross-market context leans bearish"
    else:
        title = "Cross-market context is balanced"
    strongest = sorted(items, key=lambda item: abs(item["gold_score"]), reverse=True)[:3]
    summary = "Weighted live movement: " + ", ".join(
        f'{item["id"]} {"supports" if item["gold_score"] > .1 else "pressures" if item["gold_score"] < -.1 else "is neutral for"} gold'
        for item in strongest
    ) + ". Oil carries a deliberately small weight because its inflation relationship with gold is indirect and can be offset by yields or the dollar."
    return {
        "ok": True,
        "source": "Yahoo Finance public chart data",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "pulse": {"score": round(score, 3), "sample_size": len(items), "title": title, "summary": summary},
    }


def new_york_timezone(local_date: datetime):
    if ZoneInfo is not None:
        try:
            return ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            pass
    # U.S. DST fallback: second Sunday in March through first Sunday in November.
    march = calendar.monthcalendar(local_date.year, 3)
    sundays_march = [week[calendar.SUNDAY] for week in march if week[calendar.SUNDAY]]
    november = calendar.monthcalendar(local_date.year, 11)
    sundays_november = [week[calendar.SUNDAY] for week in november if week[calendar.SUNDAY]]
    dst_start = datetime(local_date.year, 3, sundays_march[1], 2)
    dst_end = datetime(local_date.year, 11, sundays_november[0], 2)
    return timezone(timedelta(hours=-4 if dst_start <= local_date < dst_end else -5))


def parse_calendar_datetime(date_text: str, time_text: str) -> str | None:
    if not date_text or not time_text or time_text.lower() in {"all day", "tentative"}:
        return None
    parsed_date = None
    for pattern in ("%m-%d-%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            parsed_date = datetime.strptime(date_text, pattern)
            break
        except ValueError:
            continue
    if parsed_date is None:
        return None
    compact_time = time_text.lower().replace(" ", "")
    parsed_time = None
    for pattern in ("%I:%M%p", "%I%p", "%H:%M"):
        try:
            parsed_time = datetime.strptime(compact_time, pattern)
            break
        except ValueError:
            continue
    if parsed_time is None:
        return None
    local_naive = parsed_date.replace(hour=parsed_time.hour, minute=parsed_time.minute)
    aware = local_naive.replace(tzinfo=new_york_timezone(local_naive))
    return aware.astimezone(timezone.utc).isoformat()


def load_calendar() -> dict[str, Any]:
    root = ET.fromstring(fetch_bytes(CALENDAR_URL))
    now = datetime.now(timezone.utc)
    events: list[dict[str, Any]] = []
    for node in root.findall(".//event"):
        country = text_of(node, "country").upper()
        impact = text_of(node, "impact").title()
        if country != "USD" or impact not in {"High", "Medium"}:
            continue
        event_time = parse_calendar_datetime(text_of(node, "date"), text_of(node, "time"))
        if event_time:
            parsed = datetime.fromisoformat(event_time)
            if parsed < now - timedelta(hours=3):
                continue
        events.append({
            "title": html.unescape(text_of(node, "title")),
            "country": country,
            "impact": impact,
            "time_utc": event_time,
            "forecast": text_of(node, "forecast"),
            "previous": text_of(node, "previous"),
            "url": safe_external_url(text_of(node, "url"), "https://www.forexfactory.com/calendar"),
        })
    events.sort(key=lambda event: (event["time_utc"] is None, event["time_utc"] or "9999"))
    if not events:
        raise ValueError("No upcoming USD calendar events in feed")
    return {
        "ok": True,
        "source": "Forex Factory calendar feed",
        "updated_at": now.isoformat(),
        "events": events[:14],
    }


class LiqueDTHandler(SimpleHTTPRequestHandler):
    server_version = "LiqueDT/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/news":
            self.send_json(CACHE.get("news", 180, load_news))
            return
        if path == "/api/calendar":
            self.send_json(CACHE.get("calendar", 900, load_calendar))
            return
        if path == "/api/market":
            self.send_json(CACHE.get("market", 60, load_market))
            return
        if path == "/api/health":
            self.send_json({"ok": True, "service": "liquedt-gateway", "time": datetime.now(timezone.utc).isoformat()})
            return
        super().do_GET()

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' https://s3.tradingview.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; "
            "frame-src https://s.tradingview.com https://www.tradingview.com https://www.tradingview-widget.com; "
            "connect-src 'self' https://*.tradingview.com wss://*.tradingview.com; "
            "form-action 'self' https://formsubmit.co",
        )
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LiqueDT locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), LiqueDTHandler)
    print(f"LiqueDT is live at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
