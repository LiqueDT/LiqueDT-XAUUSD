"""LiqueDT local gateway: static PWA + cached, normalized market-context feeds."""

from __future__ import annotations

import argparse
import calendar
import html
import json
import math
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
CALENDAR_URLS = (
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.xml",
)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36 LiqueDT/1.7"
GOLD_TERMS = (
    "gold", "xau", "dollar", "usd", "fed", "fomc", "powell", "yield", "treasury",
    "inflation", "cpi", "pce", "jobs", "payroll", "geopolit", "war", "tariff", "oil",
    "trump", "white house", "sanction", "trade", "china", "middle east",
)

MARKET_SERIES = (
    {"id": "XAUUSD", "ticker": "GC=F", "name": "Gold futures proxy", "relation": 1.0, "weight": 0.24, "move_scale": 0.90},
    {"id": "DXY", "ticker": "DX-Y.NYB", "name": "U.S. Dollar Index", "relation": -1.0, "weight": 0.32, "move_scale": 0.50},
    {"id": "US10Y", "ticker": "^TNX", "name": "U.S. 10Y yield", "relation": -1.0, "weight": 0.30, "move_scale": 2.00},
    {"id": "WTI", "ticker": "CL=F", "name": "WTI crude oil", "relation": 0.35, "weight": 0.08, "move_scale": 2.00},
    {"id": "XAUUSDT", "ticker": "XAUT-USD", "name": "Tether Gold proxy", "relation": 1.0, "weight": 0.06, "move_scale": 0.90},
)

BULLISH_PHRASES = {
    "gold rises": 3, "gold gains": 3, "gold jumps": 3, "gold rallies": 3, "gold advances": 2,
    "gold extends gains": 3, "gold hits record": 3, "record high": 2, "all-time high": 2,
    "rate cut bets grow": 3, "rate cut odds rise": 3, "rate hike fears ease": 3, "dovish": 2,
    "lower yields": 2, "yields fall": 2, "yields drop": 2, "yields slide": 2,
    "dollar weakens": 3, "dollar falls": 3, "dollar slides": 3, "dollar retreats": 2, "weaker dollar": 2,
    "safe haven": 2, "safe-haven": 2, "geopolitical tensions": 2, "tensions escalate": 2,
    "conflict": 1, "war": 1, "uncertainty": 1, "risk-off": 2, "recession": 1,
    "tariff uncertainty": 1, "sanctions": 1, "middle east tensions": 2,
    "central bank buying": 3, "gold demand": 2, "etf inflows": 2, "inflation fears": 1,
}
BEARISH_PHRASES = {
    "gold falls": -3, "gold drops": -3, "gold slips": -2, "gold retreats": -2, "gold slides": -2,
    "gold under pressure": -2, "gold pares gains": -1, "profit-taking": -1,
    "rate cut bets fade": -3, "rate cut odds fall": -3, "rate hike odds rise": -3, "hawkish": -2,
    "higher yields": -2, "yields rise": -2, "yields climb": -2, "yields jump": -2, "yields surge": -3,
    "dollar strengthens": -3, "dollar rises": -3, "dollar gains": -3, "dollar firms": -2, "stronger dollar": -2,
    "risk-on": -1, "risk appetite improves": -1, "ceasefire": -2, "peace deal": -2, "trade deal": -1,
    "inflation hotter": -1, "hot inflation": -1, "above forecast": -1,
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


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def contains_term(value: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", value) is not None


def contains_any_term(value: str, terms: tuple[str, ...]) -> bool:
    return any(contains_term(value, term) for term in terms)


def is_gold_relevant(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", html.unescape(title).lower()).strip()
    direct_terms = ("gold", "xau", "bullion", "precious metal", "precious metals", "comex", "spot gold")
    macro_terms = ("dollar", "usd", "dxy", "fed", "fomc", "powell", "rate", "yield", "treasury", "cpi", "pce", "ppi", "inflation", "payroll", "jobs", "jobless", "unemployment", "gdp", "retail sales", "pmi", "ism")
    risk_terms = ("war", "geopolit", "conflict", "ceasefire", "tariff", "sanction", "trump", "white house", "china", "middle east", "risk-off", "risk-on")
    if contains_any_term(normalized, direct_terms):
        return True
    if contains_any_term(normalized, macro_terms) and any(term in normalized for term in ("dollar", "yield", "fed", "rate", "inflation", "cpi", "pce", "payroll", "jobs")):
        return True
    return contains_any_term(normalized, risk_terms) and any(term in normalized for term in ("safe", "haven", "risk", "tariff", "war", "conflict", "sanction", "geopolit"))


def pearson(values_a: list[float], values_b: list[float]) -> float | None:
    if len(values_a) != len(values_b) or len(values_a) < 6:
        return None
    if not all(math.isfinite(value) for value in values_a + values_b):
        return None
    mean_a = sum(values_a) / len(values_a)
    mean_b = sum(values_b) / len(values_b)
    centered_a = [value - mean_a for value in values_a]
    centered_b = [value - mean_b for value in values_b]
    denom_a = math.sqrt(sum(value * value for value in centered_a))
    denom_b = math.sqrt(sum(value * value for value in centered_b))
    if not denom_a or not denom_b or not math.isfinite(denom_a) or not math.isfinite(denom_b):
        return None
    result = sum(a * b for a, b in zip(centered_a, centered_b)) / (denom_a * denom_b)
    return result if math.isfinite(result) else None


def daily_returns(closes: list[float]) -> list[float]:
    output: list[float] = []
    for previous, current in zip(closes, closes[1:]):
        if previous and math.isfinite(previous) and math.isfinite(current):
            change = (current - previous) / previous
            if math.isfinite(change):
                output.append(change)
    return output


def rolling_corr(primary: list[float], secondary: list[float], window: int) -> float | None:
    length = min(len(primary), len(secondary))
    if length < window + 1:
        return None
    primary_returns = daily_returns(primary[-(window + 1):])
    secondary_returns = daily_returns(secondary[-(window + 1):])
    value = pearson(primary_returns, secondary_returns)
    return None if value is None or not math.isfinite(value) else round(max(-1.0, min(1.0, value)), 3)


def correlation_strength(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "unavailable"
    absolute = abs(value)
    if absolute >= 0.55:
        return "strong"
    if absolute >= 0.32:
        return "moderate"
    if absolute >= 0.18:
        return "weak"
    return "unstable"


def correlation_label(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "correlation unavailable"
    if value >= 0.18:
        return "positive correlation"
    if value <= -0.18:
        return "inverse correlation"
    return "unstable correlation"


def effective_relation(series: dict[str, Any], corr_60: float | None) -> float:
    if series["id"] == "XAUUSD":
        return 1.0
    if corr_60 is None or not math.isfinite(corr_60):
        return float(series["relation"]) * 0.50
    if abs(corr_60) < 0.18:
        return 0.0
    return corr_60


def correlation_note(series: dict[str, Any], corr_20: float | None, corr_60: float | None) -> str:
    if series["id"] == "XAUUSD":
        return "Primary gold momentum anchor"
    if corr_60 is None:
        return "Using muted macro assumption; rolling correlation unavailable"
    expected = float(series["relation"])
    confirms = corr_60 * expected > 0.12
    contradicts = corr_60 * expected < -0.12
    regime = "confirms usual macro relationship" if confirms else "is flipped versus usual macro relationship" if contradicts else "is currently unstable"
    short = f"20D {corr_20:+.2f}" if corr_20 is not None else "20D n/a"
    medium = f"60D {corr_60:+.2f}"
    return f"{medium}, {short}; {regime}"


def load_daily_closes_yahoo(ticker: str) -> list[float]:
    encoded = urllib.parse.quote(ticker, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?interval=1d&range=6mo"
    payload = json.loads(fetch_bytes(url).decode("utf-8"))
    result = payload["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [float(value) for value in closes if value is not None]


def directional_matches(normalized: str) -> list[tuple[str, int]]:
    candidates = sorted((*BULLISH_PHRASES.items(), *BEARISH_PHRASES.items()), key=lambda pair: len(pair[0]), reverse=True)
    occupied: list[tuple[int, int]] = []
    matches: list[tuple[str, int]] = []
    for phrase, weight in candidates:
        for found in re.finditer(re.escape(phrase), normalized):
            span = found.span()
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            occupied.append(span)
            matches.append((phrase, weight))
            break
    return matches


def contextual_headline_score(normalized: str) -> tuple[int, str] | None:
    if any(term in normalized for term in ("await", "preview", "watch", "steady", "flat", "little changed")) and not any(term in normalized for term in ("falls", "rises", "drops", "jumps", "surges", "retreats")):
        return None
    if any(term in normalized for term in ("safe-haven demand", "safe haven demand", "geopolitical risk", "middle east tensions", "tariff uncertainty", "central-bank buying", "central bank demand")):
        return 1, "safe-haven or structural demand support"
    if any(term in normalized for term in ("higher for longer", "resilient us data", "strong us data", "hotter-than-expected", "above expectations")):
        return -1, "rate/yield pressure from stronger U.S. data"
    if any(term in normalized for term in ("cooler-than-expected", "soft us data", "weak us data", "rate relief", "dollar weakness")):
        return 1, "rate-relief or weaker-dollar context"
    if "trump" in normalized and any(term in normalized for term in ("tariff", "sanction", "uncertainty", "trade war")):
        return 1, "policy uncertainty can support haven demand"
    if any(term in normalized for term in ("ceasefire", "peace", "risk appetite", "risk-on")):
        return -1, "risk-relief can reduce haven demand"
    return None


def headline_score(title: str) -> tuple[int, str, list[str], str, str, float]:
    normalized = re.sub(r"\s+", " ", html.unescape(title).lower())
    matches = directional_matches(normalized)
    positive = sum(weight for _, weight in matches if weight > 0)
    negative = sum(weight for _, weight in matches if weight < 0)
    score = positive + negative
    if positive and negative and abs(score) <= 1:
        score = 0
    factors: list[str] = []
    if any(term in normalized for term in ("dollar", "usd", "dxy")):
        factors.append("Dollar")
    if any(term in normalized for term in ("fed", "fomc", "rate", "yield", "treasury", "powell")):
        factors.append("Rates")
    if any(term in normalized for term in ("war", "risk", "geopolit", "conflict", "ceasefire", "safe haven", "safe-haven")):
        factors.append("Risk")
    if any(term in normalized for term in ("inflation", "cpi", "pce", "ppi", "oil", "wage")):
        factors.append("Inflation")
    if any(term in normalized for term in ("tariff", "sanction", "china", "white house", "trump")):
        factors.append("Policy")
    contextual = contextual_headline_score(normalized) if score == 0 else None
    if contextual:
        score, contextual_reason = contextual
        matches.append((contextual_reason, score))
    impact = "bullish" if score > 0 else "bearish" if score < 0 else "mixed"
    reason = headline_reason(normalized, score, factors, matches, positive, negative)
    confidence = min(0.94, 0.26 + abs(score) * 0.20 + min(len(factors), 3) * 0.09)
    if contextual and abs(score) == 1:
        confidence = max(confidence, 0.48)
    if score == 0:
        confidence = min(confidence, 0.38)
    confidence_label = "high" if confidence >= 0.70 else "medium" if confidence >= 0.46 else "low"
    return score, impact, factors, reason, confidence_label, round(confidence, 2)


def headline_reason(normalized: str, score: int, factors: list[str], matches: list[tuple[str, int]], positive: int, negative: int) -> str:
    if positive and negative and score == 0:
        return "conflicting gold-supportive and gold-negative signals in the headline"
    if not score:
        return "no reliable XAUUSD direction detected from the headline alone"
    if any(phrase in normalized for phrase in ("dollar weakens", "dollar falls", "dollar slides", "dollar retreats", "weaker dollar")):
        return "weaker-dollar language"
    if any(phrase in normalized for phrase in ("dollar strengthens", "dollar rises", "dollar gains", "dollar firms", "stronger dollar")):
        return "stronger-dollar language"
    if any(phrase in normalized for phrase in ("rate cut", "dovish", "lower yields", "yields fall", "yields drop", "yields slide")):
        return "lower-rate/yield language"
    if any(phrase in normalized for phrase in ("rate hike", "hawkish", "higher yields", "yields rise", "yields climb", "yields surge")):
        return "higher-rate/yield language"
    if any(phrase in normalized for phrase in ("safe haven", "safe-haven", "geopolitical tensions", "conflict", "uncertainty", "war", "tariff")):
        return "safe-haven or policy-risk language"
    if any(phrase in normalized for phrase in ("risk-on", "risk appetite", "ceasefire", "peace deal")):
        return "risk-relief language"
    if score > 0 and any(phrase in normalized for phrase in ("gold rises", "gold gains", "gold jumps", "gold rallies", "record high")):
        return "positive gold price-action language"
    if score < 0 and any(phrase in normalized for phrase in ("gold falls", "gold drops", "gold slips", "gold retreats", "profit-taking")):
        return "negative gold price-action language"
    if any(phrase in normalized for phrase in ("central bank buying", "gold demand", "etf inflows")):
        return "gold-demand language"
    if factors:
        return f"{', '.join(factors[:2]).lower()} context"
    strongest = sorted(matches, key=lambda item: abs(item[1]), reverse=True)
    return strongest[0][0] if strongest else "headline language"


def load_news() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    factors: dict[str, int] = {}
    seen: set[str] = set()

    for feed_url, default_source in NEWS_FEEDS:
        try:
            root = ET.fromstring(fetch_bytes(feed_url))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError):
            continue
        for node in root.findall(".//item"):
            title = re.sub(r"\s+", " ", html.unescape(text_of(node, "title"))).strip()
            source = text_of(node, "source") or default_source
            if source:
                title = re.sub(rf"\s+-\s+{re.escape(source)}$", "", title, flags=re.IGNORECASE).strip()
            title_key = re.sub(r"\s+-\s+[^-]{2,80}$", "", title).casefold()
            if not title or title_key in seen or not is_gold_relevant(title):
                continue
            seen.add(title_key)
            score, impact, item_factors, reason, confidence_label, confidence = headline_score(title)
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
            items.append({
                "title": title,
                "url": safe_external_url(text_of(node, "link"), "https://www.fxstreet.com/markets/commodities/metals/gold"),
                "source": source,
                "published": published_iso,
                "impact": impact,
                "impact_label": f"estimated {impact}",
                "impact_reason": reason,
                "confidence": confidence,
                "confidence_label": confidence_label,
                "direction_score": score,
                "factors": item_factors,
                "verified_article": False,
                "method": "headline estimate",
            })

    items.sort(key=lambda item: item["published"] or "", reverse=True)
    items = items[:18]

    if not items:
        raise ValueError("No relevant gold news items in upstream feed")

    total_score = sum(int(item.get("direction_score", 0)) for item in items)
    factors = {}
    for item in items:
        for factor in item.get("factors", []):
            factors[factor] = factors.get(factor, 0) + 1
    normalized_score = max(-1.0, min(1.0, total_score / max(4, len(items) * 1.5)))
    if normalized_score >= 0.2:
        title = "Headlines lean supportive for gold"
        summary = "Recent coverage emphasizes weaker-dollar, lower-yield, haven-demand or gold-demand language. Price may already reflect the narrative."
    elif normalized_score <= -0.2:
        title = "Headlines lean restrictive for gold"
        summary = "Recent coverage emphasizes stronger-dollar, higher-yield, risk-relief or negative gold price-action language. Cross-market confirmation still matters."
    else:
        title = "The gold narrative is balanced"
        summary = "Recent headlines contain mixed or neutral XAUUSD-sensitive language with no clear aggregate lean."

    return {
        "ok": True,
        "source": "FXStreet + attributable public news (gold filter)",
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
    histories: dict[str, list[float]] = {}
    for series in MARKET_SERIES:
        try:
            histories[str(series["ticker"])] = load_daily_closes_yahoo(str(series["ticker"]))
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, OSError, urllib.error.URLError):
            continue
    gold_history = histories.get("GC=F", [])

    for series in MARKET_SERIES:
        ticker = urllib.parse.quote(str(series["ticker"]), safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=1d"
        try:
            payload = json.loads(fetch_bytes(url).decode("utf-8"))
            result = payload["chart"]["result"][0]
            meta = result["meta"]
            price = finite_float(meta.get("regularMarketPrice"))
            previous = finite_float(meta.get("chartPreviousClose") or meta.get("previousClose"))
            if price is None or previous is None or not previous:
                continue
            change_percent = (price - previous) / previous * 100
            if not math.isfinite(change_percent):
                continue
            normalized_move = max(-1.0, min(1.0, change_percent / float(series.get("move_scale", 0.75))))
            series_history = histories.get(str(series["ticker"]), [])
            if series["id"] == "XAUUSD":
                corr_20, corr_60 = 1.0, 1.0
            else:
                corr_20 = rolling_corr(gold_history, series_history, 20)
                corr_60 = rolling_corr(gold_history, series_history, 60)
            relation_used = effective_relation(series, corr_60)
            gold_score = normalized_move * relation_used
            if not math.isfinite(relation_used) or not math.isfinite(gold_score):
                continue
            weight = float(series["weight"])
            weighted_score += gold_score * weight
            total_weight += weight
            items.append({
                "id": series["id"], "name": series["name"], "ticker": series["ticker"],
                "price": round(price, 5), "change_percent": round(change_percent, 3),
                "gold_score": round(gold_score, 3), "currency": meta.get("currency", "USD"),
                "assumed_relation": float(series["relation"]),
                "effective_relation": round(relation_used, 3),
                "correlation_20": corr_20,
                "correlation_60": corr_60,
                "correlation_strength": correlation_strength(corr_60),
                "correlation_label": correlation_label(corr_60),
                "correlation_note": correlation_note(series, corr_20, corr_60),
                "relation_source": "rolling_60d_correlation" if corr_60 is not None and series["id"] != "XAUUSD" else "primary_or_macro_fallback",
                "data_proxy": series["id"] == "XAUUSDT",
                "proxy_note": "Backend uses XAUT-USD as the closest public proxy; chart tab remains BINANCE:XAUUSDT.P." if series["id"] == "XAUUSDT" else "",
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
    summary = "Correlation-aware XAUUSD movement: " + ", ".join(
        f'{item["id"]} {"supports" if item["gold_score"] > .1 else "pressures" if item["gold_score"] < -.1 else "is neutral for"} gold ({item.get("correlation_label", "correlation n/a")})'
        for item in strongest
    ) + ". The gauge is driven by each market move multiplied by its rolling XAUUSD correlation; weak regimes are muted."
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
    # User's Forex Factory calendar is already set to Singapore time; store UTC for app rendering.
    singapore = timezone(timedelta(hours=8))
    sgt_time = parsed_date.replace(hour=parsed_time.hour, minute=parsed_time.minute, tzinfo=singapore)
    return sgt_time.astimezone(timezone.utc).isoformat()


def parse_event_number(value: str | None) -> float | None:
    if not value:
        return None
    text = html.unescape(str(value)).strip().lower().replace(",", "")
    if not text or text in {"n/a", "na", "-"}:
        return None
    multiplier = 1.0
    if text.endswith("k"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.endswith("m"):
        multiplier, text = 1_000_000.0, text[:-1]
    elif text.endswith("b"):
        multiplier, text = 1_000_000_000.0, text[:-1]
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) * multiplier if match else None


def calendar_result_effect(title: str, actual: str, forecast: str, previous: str) -> dict[str, Any]:
    normalized = title.lower()
    if not actual:
        return {"status": "pending", "bias": "pending", "score": 0.0, "reason": "waiting for actual result"}
    actual_value = parse_event_number(actual)
    benchmark_value = parse_event_number(forecast) if forecast else parse_event_number(previous)
    if actual_value is None or benchmark_value is None:
        return {"status": "released", "bias": "mixed", "score": 0.0, "reason": "actual result released; numeric surprise unavailable"}
    threshold = max(abs(benchmark_value) * 0.005, 0.01)
    if abs(actual_value - benchmark_value) <= threshold:
        return {"status": "released", "bias": "mixed", "score": 0.0, "reason": "actual was broadly in line with forecast"}
    hotter = actual_value > benchmark_value
    inflation_terms = ("cpi", "pce", "ppi", "inflation", "average hourly earnings", "wages")
    slack_terms = ("unemployment rate", "unemployment claims", "jobless claims")
    jobs_terms = ("non-farm", "nonfarm", "payroll", "adp", "jolts", "job openings")
    growth_terms = ("retail sales", "gdp", "pmi", "ism", "consumer confidence", "consumer sentiment", "durable goods", "factory orders", "industrial production", "housing", "home sales")
    if any(term in normalized for term in inflation_terms):
        return {"status": "released", "bias": "bearish" if hotter else "bullish", "score": -0.70 if hotter else 0.70, "reason": "hotter inflation/wages can lift yields and the dollar" if hotter else "cooler inflation/wages can ease yields and the dollar"}
    if any(term in normalized for term in slack_terms):
        return {"status": "released", "bias": "bullish" if hotter else "bearish", "score": 0.50 if hotter else -0.50, "reason": "more labour slack can support rate-cut expectations" if hotter else "tighter labour data can keep yields firm"}
    if any(term in normalized for term in jobs_terms):
        return {"status": "released", "bias": "bearish" if hotter else "bullish", "score": -0.50 if hotter else 0.50, "reason": "stronger jobs can revive Fed/yield pressure" if hotter else "softer jobs can support rate-relief expectations"}
    if any(term in normalized for term in growth_terms):
        return {"status": "released", "bias": "bearish" if hotter else "bullish", "score": -0.35 if hotter else 0.35, "reason": "stronger growth can support USD/yields and reduce haven demand" if hotter else "weaker growth can support rate-relief or haven demand"}
    return {"status": "released", "bias": "mixed", "score": 0.0, "reason": "result released; gold effect depends on USD and yield reaction"}


def calendar_relevance(title: str) -> tuple[str, str] | None:
    normalized = title.lower()
    if any(term in normalized for term in ("fomc", "federal funds rate", "interest rate decision", "fed chair", "powell")):
        return "Critical", "Fed policy can reprice the dollar, yields and gold immediately"
    if any(term in normalized for term in ("core cpi", "cpi ", "consumer price index", "core pce", "pce price", "non-farm", "nonfarm", "unemployment rate", "average hourly earnings")):
        return "Critical", "Inflation or labour data can rapidly shift USD and rate expectations"
    if any(term in normalized for term in ("ppi", "retail sales", "ism manufacturing", "ism services", "gdp", "jolts", "unemployment claims", "jobless claims", "flash manufacturing pmi", "flash services pmi", "philly fed", "empire state")):
        return "High", "Growth or inflation data can move yields, the dollar and gold"
    if any(term in normalized for term in ("consumer confidence", "consumer sentiment", "inflation expectations", "adp", "durable goods", "factory orders", "industrial production", "housing starts", "building permits", "new home sales", "pending home sales", "fed member", "treasury auction")):
        return "Watch", "Secondary macro signal that matters when it changes the USD or rates narrative"
    if any(term in normalized for term in ("president trump speaks", "president speaks", "white house")):
        return "Watch", "Policy headlines can affect safe-haven demand and the dollar"
    return None


def calendar_pulse(events: list[dict[str, Any]]) -> dict[str, Any]:
    released = [event for event in events if event.get("result_status") == "released" and event.get("result_bias") in {"bullish", "bearish"}]
    released.sort(key=lambda event: event.get("time_utc") or "", reverse=True)
    if not released:
        return {"score": 0.0, "sample_size": 0, "title": "No fresh USD result yet", "summary": "Upcoming events are on watch, but no released result is currently biasing gold.", "factors": ["Event risk"], "latest_result": None}
    score = max(-1.0, min(1.0, sum(float(event.get("result_score") or 0) for event in released) / max(1, len(released))))
    latest = released[0]
    read = "bullish" if score >= 0.18 else "bearish" if score <= -0.18 else "mixed"
    return {"score": round(score, 3), "sample_size": len(released), "title": f"Fresh USD result leans {read} for gold", "summary": f"Latest result: {latest.get('title')} actual {latest.get('actual') or 'released'} vs forecast {latest.get('forecast') or 'n/a'}; {latest.get('result_reason')}", "factors": ["USD result", latest.get("gold_relevance") or "Event risk"], "latest_result": latest}


def load_calendar() -> dict[str, Any]:
    roots: list[ET.Element] = []
    errors: list[str] = []
    for url in CALENDAR_URLS:
        try:
            roots.append(ET.fromstring(fetch_bytes(url)))
        except (OSError, ValueError, ET.ParseError, urllib.error.URLError) as exc:
            errors.append(f"{url}: {type(exc).__name__}")
    if not roots:
        raise ValueError("; ".join(errors) or "Calendar feeds unavailable")

    now = datetime.now(timezone.utc)
    events: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str, str]] = set()
    for root in roots:
        for node in root.findall(".//event"):
            country = text_of(node, "country").upper()
            impact = text_of(node, "impact").title()
            if country != "USD" or impact not in {"High", "Medium"}:
                continue
            title = html.unescape(text_of(node, "title"))
            relevance = calendar_relevance(title)
            if relevance is None:
                continue
            event_time = parse_calendar_datetime(text_of(node, "date"), text_of(node, "time"))
            actual = text_of(node, "actual")
            forecast = text_of(node, "forecast")
            previous = text_of(node, "previous")
            if event_time:
                parsed = datetime.fromisoformat(event_time)
                keep_recent_result = bool(actual) and parsed >= now - timedelta(hours=36)
                if parsed < now - timedelta(hours=3) and not keep_recent_result:
                    continue
            event_key = (title, event_time or "", actual)
            if event_key in seen_events:
                continue
            seen_events.add(event_key)
            result = calendar_result_effect(title, actual, forecast, previous)
            events.append({
                "title": title,
                "country": country,
                "impact": impact,
                "gold_relevance": relevance[0],
                "gold_reason": relevance[1],
                "time_utc": event_time,
                "actual": actual,
                "forecast": forecast,
                "previous": previous,
                "result_status": result["status"],
                "result_bias": result["bias"],
                "result_score": result["score"],
                "result_reason": result["reason"],
                "url": safe_external_url(text_of(node, "url"), "https://www.forexfactory.com/calendar"),
            })
    events.sort(key=lambda event: (event["time_utc"] is None, event["time_utc"] or "9999"))
    if not events:
        raise ValueError("No upcoming or fresh USD calendar events in feed")
    selected = events[:14]
    return {
        "ok": True,
        "source": "Forex Factory calendar feed this week + next week (SGT input, UTC normalized)",
        "updated_at": now.isoformat(),
        "events": selected,
        "pulse": calendar_pulse(selected),
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
