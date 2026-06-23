"use strict";

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const SINGAPORE_TZ = "Asia/Singapore";
const formatterCache = new Map();

const sessions = [
  { id: "asia", timeZone: "Asia/Singapore", openHour: 8, closeHour: 16 },
  { id: "london", timeZone: "Europe/London", openHour: 8, closeHour: 17 },
  { id: "new-york", timeZone: "America/New_York", openHour: 8, closeHour: 17 }
];

const widgetSymbols = {
  "OANDA:XAUUSD": { name: "Gold / U.S. Dollar", tag: "PRECIOUS METALS" },
  "CAPITALCOM:DXY": { name: "U.S. Dollar Index", tag: "CURRENCY · CAPITAL.COM" },
  "OANDA:USB10YUSD": { name: "U.S. 10Y Bond Yield", tag: "RATES · OANDA" },
  "TVC:USOIL": { name: "WTI Crude Oil", tag: "ENERGY" },
  "BINANCE:XAUUSDT.P": { name: "XAU / TetherUS Perpetual", tag: "BINANCE · PERPETUAL" }
};

let activeSymbol = "OANDA:XAUUSD";
let latestRefresh = null;
let latestMarketPulse = null;
let latestNewsPulse = null;
let latestCalendar = null;
const WIDGETS_DISABLED = new URLSearchParams(location.search).has("no-widgets");
const healthState = { market: "checking", charts: "checking", news: "checking", calendar: "checking" };
const healthMeta = { market: null, charts: null, news: null, calendar: null };
const healthLabels = { market: "Markets", charts: "Charts", news: "News", calendar: "Calendar" };

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatter(timeZone, options) {
  const key = `${timeZone}:${JSON.stringify(options)}`;
  if (!formatterCache.has(key)) {
    formatterCache.set(key, new Intl.DateTimeFormat("en-GB", { timeZone, ...options }));
  }
  return formatterCache.get(key);
}

function zonedParts(date, timeZone) {
  const values = Object.fromEntries(
    formatter(timeZone, {
      weekday: "short", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
    }).formatToParts(date).filter(part => part.type !== "literal").map(part => [part.type, part.value])
  );
  return {
    year: Number(values.year), month: Number(values.month), day: Number(values.day),
    hour: Number(values.hour), minute: Number(values.minute), second: Number(values.second),
    weekday: values.weekday
  };
}

function zonedDateTimeToUtc(year, month, day, hour, minute, timeZone) {
  const desired = Date.UTC(year, month - 1, day, hour, minute, 0);
  let guess = desired;
  for (let i = 0; i < 3; i += 1) {
    const parts = zonedParts(new Date(guess), timeZone);
    const rendered = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second);
    guess += desired - rendered;
  }
  return new Date(guess);
}

function calendarDate(parts, offsetDays = 0) {
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + offsetDays));
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() + 1, day: date.getUTCDate(), weekday: date.getUTCDay() };
}

function countdown(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  const clock = [hours, minutes, secs].map(value => String(value).padStart(2, "0")).join(":");
  return days ? `${days}d ${clock}` : clock;
}

function sessionState(now, config) {
  const parts = zonedParts(now, config.timeZone);
  const weekday = calendarDate(parts).weekday;
  const minuteOfDay = parts.hour * 60 + parts.minute;
  const opens = config.openHour * 60;
  const closes = config.closeHour * 60;
  const isWeekday = weekday >= 1 && weekday <= 5;
  const open = isWeekday && minuteOfDay >= opens && minuteOfDay < closes;
  let target;

  if (open) {
    target = zonedDateTimeToUtc(parts.year, parts.month, parts.day, config.closeHour, 0, config.timeZone);
  } else {
    let offset = isWeekday && minuteOfDay < opens ? 0 : 1;
    while (true) {
      const candidate = calendarDate(parts, offset);
      if (candidate.weekday >= 1 && candidate.weekday <= 5) {
        target = zonedDateTimeToUtc(candidate.year, candidate.month, candidate.day, config.openHour, 0, config.timeZone);
        break;
      }
      offset += 1;
    }
  }

  const displayParts = zonedParts(open ? now : target, config.timeZone);
  const openSg = formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(zonedDateTimeToUtc(displayParts.year, displayParts.month, displayParts.day, config.openHour, 0, config.timeZone));
  const closeSg = formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .format(zonedDateTimeToUtc(displayParts.year, displayParts.month, displayParts.day, config.closeHour, 0, config.timeZone));

  return { open, target, openSg, closeSg };
}

function nextSundayAt(parts, hour, timeZone) {
  const today = calendarDate(parts);
  let offset = (7 - today.weekday) % 7;
  if (offset === 0 && parts.hour >= hour) offset = 7;
  const candidate = calendarDate(parts, offset);
  return zonedDateTimeToUtc(candidate.year, candidate.month, candidate.day, hour, 0, timeZone);
}

function goldMarketState(now) {
  const timeZone = "America/New_York";
  const parts = zonedParts(now, timeZone);
  const date = calendarDate(parts);
  const minute = parts.hour * 60 + parts.minute;
  const maintenanceStart = 17 * 60;
  const maintenanceEnd = 18 * 60;
  let open = false;

  if (date.weekday === 0) open = minute >= maintenanceEnd;
  if (date.weekday >= 1 && date.weekday <= 4) open = minute < maintenanceStart || minute >= maintenanceEnd;
  if (date.weekday === 5) open = minute < maintenanceStart;

  let target;
  let label;
  if (open) {
    if (date.weekday === 0 || minute >= maintenanceEnd) {
      const tomorrow = calendarDate(parts, 1);
      target = zonedDateTimeToUtc(tomorrow.year, tomorrow.month, tomorrow.day, 17, 0, timeZone);
    } else {
      target = zonedDateTimeToUtc(parts.year, parts.month, parts.day, 17, 0, timeZone);
    }
    label = date.weekday === 5 ? "Until weekly close" : "Until daily pause";
  } else if (date.weekday >= 1 && date.weekday <= 4 && minute >= maintenanceStart && minute < maintenanceEnd) {
    target = zonedDateTimeToUtc(parts.year, parts.month, parts.day, 18, 0, timeZone);
    label = "Until rollover reopens";
  } else {
    target = nextSundayAt(parts, 18, timeZone);
    label = "Until weekly open";
  }
  return { open, target, label };
}

function updateClocks() {
  const now = new Date();
  $("#sgTime").textContent = formatter(SINGAPORE_TZ, {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  }).format(now);
  $("#sgDate").textContent = `${formatter(SINGAPORE_TZ, { weekday: "short", day: "2-digit", month: "short" }).format(now)} · SGT`;

  const market = goldMarketState(now);
  $("#marketState").textContent = market.open ? "XAUUSD open" : "XAUUSD closed";
  $("#marketStateDetail").textContent = market.open ? "Indicative OTC window" : "Weekend / rollover";
  $("#marketCountdown").textContent = countdown(market.target - now);
  $("#marketCountdownLabel").textContent = market.label;
  $("#marketDot").className = `market-dot ${market.open ? "open" : "closed"}`;

  sessions.forEach(session => {
    const state = sessionState(now, session);
    const card = $(`[data-session="${session.id}"]`);
    card.classList.toggle("open", state.open);
    card.querySelector(".session-status").textContent = state.open ? "OPEN NOW" : "CLOSED";
    card.querySelector(".session-timer strong").textContent = countdown(state.target - now);
    card.querySelector(".session-timer small").textContent = state.open ? "Until close" : "Until open";
    card.querySelector(".session-local-time")?.replaceChildren(`${state.openSg}–${state.closeSg} SGT`);
  });
}

function renderHealthSummary() {
  const values = Object.values(healthState);
  const dot = $("#dataHealthDot");
  const summary = $("#dataHealthSummary");
  let state = "live";
  let title = "All data current";
  let detail = "All monitored sources are responding";
  if (values.includes("checking")) {
    state = "checking";
    title = "Checking live data";
    detail = "Connecting markets, charts, news and calendar";
  } else if (values.includes("offline")) {
    state = "offline";
    title = "Some data is unavailable";
    detail = "Unavailable sources are clearly marked below";
  } else if (values.includes("delayed")) {
    state = "delayed";
    const delayed = Object.entries(healthState)
      .filter(([, value]) => value === "delayed")
      .map(([key]) => healthMeta[key]?.summary || `${healthLabels[key] || key}: snapshot`);
    title = delayed.some(text => text.toLowerCase().includes("stale")) ? "Using stale snapshot data" : "Live with snapshot data";
    detail = delayed.length ? delayed.join(" · ") : "A cached or backup source is currently active";
  }
  dot.className = `health-dot ${state}`;
  summary.textContent = title;
  $("#dataFreshness").textContent = detail;
}

function setHealth(key, state, label, meta) {
  healthState[key] = state;
  if (arguments.length >= 4) healthMeta[key] = meta;
  const element = $(`#${key}Health`);
  element.className = state;
  element.textContent = label || ({ live: "Live", delayed: "Backup", offline: "Offline", checking: "Checking" }[state]);
  element.title = healthMeta[key]?.detail || "";
  renderHealthSummary();
}

function mountWidget(container, scriptName, config, healthKey = null) {
  container.replaceChildren();
  const shell = document.createElement("div");
  shell.className = "tradingview-widget-container__widget";
  const script = document.createElement("script");
  script.type = "text/javascript";
  script.src = `https://s3.tradingview.com/external-embedding/${scriptName}`;
  script.async = true;
  script.textContent = JSON.stringify(config);
  if (healthKey) {
    setHealth(healthKey, "checking", "Loading");
    script.addEventListener("load", () => setHealth(healthKey, "live", "Live"));
    script.addEventListener("error", () => setHealth(healthKey, "offline", "Unavailable"));
  }
  container.append(shell, script);
}

function mountTicker() {
  const mobile = window.matchMedia("(max-width: 560px)").matches;
  const symbols = [
    { proName: "OANDA:XAUUSD", title: "Gold" },
    { proName: "CAPITALCOM:DXY", title: "Dollar index" },
    { proName: "OANDA:USB10YUSD", title: "U.S. 10Y" },
    { proName: "TVC:USOIL", title: "WTI crude" },
    { proName: "BINANCE:XAUUSDT.P", title: "XAU / USDT perp" },
    { proName: "OANDA:XAGUSD", title: "Silver" }
  ];
  const config = {
    symbols: mobile ? symbols.slice(0, 4) : symbols,
    showSymbolLogo: true, isTransparent: true, displayMode: mobile ? "regular" : "adaptive", colorTheme: "dark", locale: "en"
  };
  mountWidget($("#tickerWidget"), "embed-widget-ticker-tape.js", config);
  if (mobile) $("#tickerWidgetClone").replaceChildren();
  else mountWidget($("#tickerWidgetClone"), "embed-widget-ticker-tape.js", config);
}

function mountChart(symbol = activeSymbol) {
  const compactChart = window.matchMedia("(max-width: 820px)").matches;
  activeSymbol = symbol;
  const meta = widgetSymbols[symbol];
  $("#activeMarketName").textContent = meta.name;
  $("#activeMarketTag").textContent = meta.tag;
  $$("#marketTabs button").forEach(button => {
    const active = button.dataset.symbol === symbol;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (WIDGETS_DISABLED) {
    $("#marketChart").innerHTML = `<div class="widget-placeholder">${escapeHtml(meta.name)} chart paused for interface testing</div>`;
    return;
  }
  mountWidget($("#marketChart"), "embed-widget-advanced-chart.js", {
    autosize: true, width: "100%", height: "100%", symbol, interval: "15", timezone: "Asia/Singapore", theme: "dark",
    style: "1", locale: "en", backgroundColor: "rgba(16, 23, 31, 1)",
    gridColor: "rgba(226, 232, 240, 0.055)", hide_top_toolbar: false,
    hide_side_toolbar: compactChart, hide_legend: false, withdateranges: true,
    allow_symbol_change: false, save_image: false, calendar: false, support_host: "https://www.tradingview.com"
  }, "charts");
}

async function fetchJson(name) {
  const candidates = [`api/${name}`, `data/${name}.json`];
  let lastError = new Error("No data source responded");
  for (const path of candidates) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const url = new URL(path, document.baseURI);
      url.searchParams.set("v", String(Date.now()));
      const response = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("json")) throw new Error("Response was not JSON");
      const payload = await response.json();
      if (path.startsWith("data/")) payload.static_snapshot = true;
      return payload;
    } catch (error) {
      lastError = error;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

function relativeTime(value) {
  if (!value) return "Recently";
  const seconds = Math.round((new Date(value) - new Date()) / 1000);
  const absolute = Math.abs(seconds);
  const formatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (absolute < 60) return formatter.format(Math.round(seconds), "second");
  if (absolute < 3600) return formatter.format(Math.round(seconds / 60), "minute");
  if (absolute < 86400) return formatter.format(Math.round(seconds / 3600), "hour");
  return formatter.format(Math.round(seconds / 86400), "day");
}

function ageLabel(value) {
  return timestamp(value) ? relativeTime(value) : "age unknown";
}

function timestamp(value) {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function firstTimestamp(...values) {
  for (const value of values) {
    const date = timestamp(value);
    if (date) return date;
  }
  return null;
}

function sgtClock(value) {
  const date = timestamp(value);
  if (!date) return "time unknown";
  return formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
}

function sgtStamp(value) {
  const date = timestamp(value);
  if (!date) return "time unknown";
  const day = formatter(SINGAPORE_TZ, { weekday: "short", day: "2-digit", month: "short" }).format(date);
  return `${day} ${sgtClock(date)} SGT`;
}

function statusFreshness(payload, sourceName, options = {}) {
  const backup = Boolean(payload?.stale || payload?.static_snapshot);
  const stale = Boolean(payload?.stale);
  const dataAt = firstTimestamp(payload?.snapshot_data_at, payload?.updated_at, payload?.snapshot_refreshed_at, payload?.snapshot_generated_at);
  const generatedAt = firstTimestamp(payload?.snapshot_generated_at, payload?.snapshot_attempted_at);
  const contentAt = firstTimestamp(options.contentAt);
  const stampTarget = dataAt || generatedAt;
  const stamp = stampTarget ? `${sgtClock(stampTarget)} SGT` : "time unknown";
  const badge = backup ? `${stale ? "STALE " : ""}SNAPSHOT · ${sgtClock(stampTarget)}` : (options.liveBadge || "LIVE DATA");
  const health = backup ? `Snap ${sgtClock(stampTarget)}` : (options.liveHealth || "Live");
  const contentNote = contentAt && options.contentLabel
    ? ` · ${options.contentLabel} ${relativeTime(contentAt)}`
    : "";

  let detail;
  if (backup && stale) {
    const retry = generatedAt ? `; GitHub retry ${sgtStamp(generatedAt)}` : "";
    detail = `Stale ${sourceName.toLowerCase()} snapshot from ${sgtStamp(stampTarget)} (${ageLabel(stampTarget)})${retry}${contentNote}`;
  } else if (backup) {
    const built = generatedAt ? `; built ${sgtStamp(generatedAt)}` : "";
    detail = `${sourceName} snapshot from ${sgtStamp(stampTarget)} (${ageLabel(stampTarget)})${built}${contentNote}`;
  } else {
    detail = `${sourceName} live source updated ${sgtStamp(stampTarget)} (${ageLabel(stampTarget)})${contentNote}`;
  }

  const summaryBits = [`${sourceName}: ${stale ? "stale " : ""}${stamp}`];
  if (contentAt && options.contentLabel) summaryBits.push(`${options.contentLabel} ${relativeTime(contentAt)}`);
  const footer = backup
    ? `${stale ? "Stale snapshot" : "Snapshot"} from ${sgtStamp(stampTarget)}${generatedAt && generatedAt.getTime() !== stampTarget?.getTime() ? ` · GitHub checked ${sgtStamp(generatedAt)}` : ""}${contentNote}`
    : `Live source updated ${sgtStamp(stampTarget)}${contentNote}`;

  return {
    backup,
    stale,
    dataAt,
    generatedAt,
    contentAt,
    badge,
    health,
    detail,
    summary: summaryBits.join(" / "),
    footer,
  };
}

function eventDay(value) {
  if (!value) return "Date TBC";
  const date = new Date(value);
  const today = formatter(SINGAPORE_TZ, { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const event = formatter(SINGAPORE_TZ, { year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  const tomorrow = formatter(SINGAPORE_TZ, { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(Date.now() + 86400000));
  if (event === today) return "Today";
  if (event === tomorrow) return "Tomorrow";
  return formatter(SINGAPORE_TZ, { weekday: "short", day: "2-digit", month: "short" }).format(date);
}

function eventDayLong(value) {
  if (!value) return "Date to be confirmed";
  const date = new Date(value);
  const relative = eventDay(value);
  const full = formatter(SINGAPORE_TZ, { weekday: "long", day: "2-digit", month: "short" }).format(date);
  return relative === "Today" || relative === "Tomorrow" ? `${relative} · ${full}` : full;
}

function sentiment(score) {
  const value = Math.max(-1, Math.min(1, Number(score) || 0));
  if (value >= .18) return { key: "bullish", label: "Bullish", phrase: "leans bullish" };
  if (value <= -.18) return { key: "bearish", label: "Bearish", phrase: "leans bearish" };
  return { key: "balanced", label: "Balanced", phrase: "is balanced" };
}

function setNeedle(selector, score) {
  $(selector).style.left = `${50 + Math.max(-1, Math.min(1, Number(score) || 0)) * 42}%`;
}

function formatMarketValue(item) {
  const price = Number(item.price);
  const change = Number(item.change_percent);
  if (!Number.isFinite(price) || !Number.isFinite(change)) return "Live value unavailable";
  const decimals = price >= 100 ? 2 : price >= 10 ? 3 : 4;
  return `${price.toLocaleString("en-US", { maximumFractionDigits: decimals })} · ${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
}

function goldEffect(item) {
  const read = sentiment(item.gold_score);
  if (item.id === "WTI") {
    if (read.key === "bullish") return "Inflation-supportive";
    if (read.key === "bearish") return "Disinflationary pressure";
    return "Indirect / balanced";
  }
  return read.label;
}

function renderMarket(payload) {
  if (!payload.ok || !payload.items?.length || !payload.pulse) {
    latestMarketPulse = null;
    setHealth("market", "offline", "No data");
    const status = $("#marketPulseStatus");
    status.className = "source-status offline";
    status.textContent = "NO DATA";
    $("#marketPulseTitle").textContent = "Cross-market read unavailable";
    $("#marketPulseSummary").textContent = "No verified market snapshot is available, so LiqueDT will not show a directional assumption.";
    setNeedle("#marketPulseNeedle", 0);
    $$('[data-driver]').forEach(card => {
      const metric = card.querySelector(".driver-market-read");
      if (!metric) return;
      metric.className = "driver-market-read offline";
      metric.textContent = "Market context unavailable";
    });
    renderTotalPulse();
    return false;
  }

  const backup = Boolean(payload.stale || payload.static_snapshot);
  const freshness = statusFreshness(payload, "Markets", { liveBadge: "LIVE DATA" });
  latestMarketPulse = { ...payload.pulse, backup, freshness };
  setHealth("market", backup ? "delayed" : "live", freshness.health, freshness);
  const status = $("#marketPulseStatus");
  status.className = `source-status ${backup ? "delayed" : "live"}`;
  status.textContent = freshness.badge;
  status.title = freshness.detail;
  setNeedle("#marketPulseNeedle", payload.pulse.score);
  $("#marketPulseTitle").textContent = payload.pulse.title || `Cross-market context ${sentiment(payload.pulse.score).phrase}`;
  $("#marketPulseSummary").textContent = payload.pulse.summary || "Weighted from gold momentum, the dollar, U.S. yields and oil.";

  payload.items.forEach(item => {
    const card = $(`[data-driver="${item.id}"]`);
    if (!card) return;
    let metric = card.querySelector(".driver-market-read");
    if (!metric) {
      metric = document.createElement("div");
      metric.className = "driver-market-read";
      card.querySelector("h3").insertAdjacentElement("afterend", metric);
    }
    const read = sentiment(item.gold_score);
    metric.className = `driver-market-read ${read.key}`;
    metric.textContent = `${formatMarketValue(item)} · Gold effect: ${goldEffect(item)}`;
  });
  renderTotalPulse();
  return true;
}

function renderTotalPulse() {
  const parts = [];
  let weighted = 0;
  let weight = 0;
  if (latestMarketPulse) {
    weighted += Number(latestMarketPulse.score || 0) * .65;
    weight += .65;
    parts.push(`Cross-market context ${sentiment(latestMarketPulse.score).phrase}`);
  }
  if (latestNewsPulse) {
    weighted += Number(latestNewsPulse.score || 0) * .35;
    weight += .35;
    parts.push(`news narrative ${sentiment(latestNewsPulse.score).phrase}`);
  }

  const status = $("#totalPulseStatus");
  if (!weight) {
    status.className = "source-status offline";
    status.textContent = "NO DATA";
    $("#totalPulseTitle").textContent = "Total context unavailable";
    $("#totalPulseSummary").textContent = "LiqueDT needs at least one verified market or news source before showing an assumption.";
    setNeedle("#totalPulseNeedle", 0);
    return;
  }

  const score = weighted / weight;
  const read = sentiment(score);
  const partial = !latestMarketPulse || !latestNewsPulse || latestMarketPulse?.backup || latestNewsPulse?.backup;
  const highImpact = latestCalendar?.events?.filter(event => event.impact === "High").length || 0;
  status.className = `source-status ${partial ? "delayed" : "live"}`;
  status.textContent = partial ? "PARTIAL / SNAPSHOT" : "LIVE COMBINED";
  status.title = [latestMarketPulse?.freshness?.detail, latestNewsPulse?.freshness?.detail].filter(Boolean).join(" · ");
  setNeedle("#totalPulseNeedle", score);
  $("#totalPulseTitle").textContent = `Total XAUUSD context ${read.phrase}`;
  $("#totalPulseSummary").textContent = `${parts.join("; ")}. ${highImpact ? `${highImpact} high-impact USD event${highImpact === 1 ? " is" : "s are"} ahead, which can quickly invalidate the current read.` : "No listed high-impact USD event is currently adding event risk."}`;
  const factors = [latestMarketPulse && `Markets: ${sentiment(latestMarketPulse.score).label}`, latestNewsPulse && `News: ${sentiment(latestNewsPulse.score).label}`, highImpact && `${highImpact} high-impact event${highImpact === 1 ? "" : "s"}`].filter(Boolean);
  $("#totalPulseFactors").innerHTML = factors.map(factor => `<span>${escapeHtml(factor)}</span>`).join("");
}

function renderCalendar(payload) {
  const status = $("#calendarStatus");
  if (!payload.ok || !payload.events?.length) {
    latestCalendar = null;
    if (!WIDGETS_DISABLED) {
      status.className = "source-status delayed";
      status.textContent = "LIVE BACKUP";
      status.title = "Primary calendar feed is unavailable; TradingView calendar widget is loaded as a backup.";
      setHealth("calendar", "delayed", "Backup", { summary: "Calendar: live backup widget", detail: status.title });
      $("#calendarFreshnessNote").textContent = "Live backup widget · USD high/medium impact";
      mountWidget($("#calendarList"), "embed-widget-events.js", {
        colorTheme: "dark", isTransparent: true, width: "100%", height: 385,
        locale: "en", importanceFilter: "0,1", countryFilter: "us"
      });
    } else {
      status.className = "source-status offline";
      status.textContent = "UNAVAILABLE";
      status.title = "Calendar feed is unavailable.";
      setHealth("calendar", "offline", "Unavailable", null);
      $("#calendarFreshnessNote").textContent = "Calendar feed unavailable";
      $("#calendarList").innerHTML = '<div class="empty-feed">The calendar feed is unavailable right now. Use the full calendar link below before making time-sensitive decisions.</div>';
    }
    renderTotalPulse();
    return false;
  }
  latestCalendar = payload;
  const backup = Boolean(payload.stale || payload.static_snapshot);
  const freshness = statusFreshness(payload, "Calendar", { liveBadge: "LIVE FEED" });
  status.className = `source-status ${backup ? "delayed" : "live"}`;
  status.textContent = freshness.badge;
  status.title = freshness.detail;
  setHealth("calendar", backup ? "delayed" : "live", freshness.health, freshness);
  $("#calendarFreshnessNote").textContent = `${freshness.footer} · USD high/medium impact`;
  const groups = new Map();
  payload.events.slice(0, 12).forEach(event => {
    const key = event.time_utc
      ? formatter(SINGAPORE_TZ, { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(event.time_utc))
      : "TBC";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  });
  $("#calendarList").innerHTML = [...groups.values()].map(events => {
    const label = eventDayLong(events[0].time_utc);
    const rows = events.map(event => {
      const time = event.time_utc
        ? formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(event.time_utc))
        : "TBC";
      const values = [event.forecast && `Fcst ${event.forecast}`, event.previous && `Prev ${event.previous}`].filter(Boolean).join(" · ") || "Details pending";
      return `<article class="calendar-item">
        <div class="calendar-time"><strong>${escapeHtml(time)}</strong><small>SGT</small></div>
        <div class="calendar-copy"><h3>${escapeHtml(event.title)}</h3><p>USD · ${escapeHtml(values)}</p></div>
        <span class="impact ${event.impact === "High" ? "high" : "medium"}"><i></i>${escapeHtml(event.impact)}</span>
      </article>`;
    }).join("");
    return `<section class="calendar-day-group"><div class="calendar-day-label">${escapeHtml(label)}</div>${rows}</section>`;
  }).join("");
  renderTotalPulse();
  return true;
}

function renderNews(payload) {
  const status = $("#newsStatus");
  if (!payload.ok || !payload.items?.length) {
    if (!WIDGETS_DISABLED) {
      status.className = "source-status delayed";
      status.textContent = "LIVE BACKUP";
      status.title = "Primary news feed is unavailable; TradingView headline widget is loaded as a backup.";
      setHealth("news", "delayed", "Backup", { summary: "News: live backup widget", detail: status.title });
      $("#newsFreshnessNote").textContent = "Live backup widget · source attribution shown per story";
      mountWidget($("#newsList"), "embed-widget-timeline.js", {
        feedMode: "symbol", symbol: "OANDA:XAUUSD", colorTheme: "dark",
        isTransparent: true, displayMode: "regular", width: "100%", height: 385, locale: "en"
      });
    } else {
      status.className = "source-status offline";
      status.textContent = "UNAVAILABLE";
      status.title = "News feed is unavailable.";
      setHealth("news", "offline", "Unavailable", null);
      $("#newsFreshnessNote").textContent = "News feed unavailable";
      $("#newsList").innerHTML = '<div class="empty-feed">Live headlines could not be reached. LiqueDT will retry automatically; open the source link below for a direct check.</div>';
    }
    renderPulse(null);
    return false;
  }
  const backup = Boolean(payload.stale || payload.static_snapshot);
  const freshness = statusFreshness(payload, "News", {
    contentAt: payload.items?.[0]?.published,
    contentLabel: "latest headline",
    liveBadge: "LIVE FEED"
  });
  status.className = `source-status ${backup ? "delayed" : "live"}`;
  status.textContent = freshness.badge;
  status.title = freshness.detail;
  setHealth("news", backup ? "delayed" : "live", freshness.health, freshness);
  $("#newsFreshnessNote").textContent = `${freshness.footer} · source attribution shown per story`;
  $("#newsList").innerHTML = payload.items.slice(0, 18).map(item => `<a class="news-item" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
    <span class="news-effect ${escapeHtml(item.impact)}">${escapeHtml(item.impact || "mixed")}</span>
    <span class="news-copy"><h3>${escapeHtml(item.title)}</h3><p><span>${escapeHtml(item.source || "FXStreet")}</span><span>·</span><span>${escapeHtml(relativeTime(item.published))}</span></p></span>
  </a>`).join("");
  renderPulse(payload.pulse, backup, freshness);
  return true;
}

function renderPulse(pulse, backup = false, freshness = null) {
  const status = $("#pulseStatus");
  if (!pulse) {
    latestNewsPulse = null;
    status.className = "source-status offline";
    status.textContent = "NO FEED";
    $("#pulseTitle").textContent = "Narrative read unavailable";
    $("#pulseSummary").textContent = "Live headlines are not reachable. No directional assumption is shown when the source cannot be verified.";
    $("#pulseNeedle").style.left = "50%";
    renderTotalPulse();
    return;
  }
  const score = Math.max(-1, Math.min(1, Number(pulse.score) || 0));
  latestNewsPulse = { ...pulse, score, backup, freshness };
  status.className = `source-status ${backup ? "delayed" : "live"}`;
  status.textContent = backup && freshness ? `${freshness.badge} · ${pulse.sample_size || 0}` : `${pulse.sample_size || 0} HEADLINES`;
  status.title = freshness?.detail || "";
  $("#pulseNeedle").style.left = `${50 + score * 42}%`;
  $("#pulseTitle").textContent = pulse.title || "Balanced narrative";
  $("#pulseSummary").textContent = pulse.summary || "Recent headlines contain mixed gold-sensitive language.";
  const factors = pulse.factors?.length ? pulse.factors : ["Dollar", "Rates", "Risk"];
  $("#pulseFactors").innerHTML = factors.slice(0, 4).map(factor => `<span>${escapeHtml(factor)}</span>`).join("");
  renderTotalPulse();
}

function updateFreshness(successCount) {
  latestRefresh = new Date();
  $("#lastChecked").textContent = `${formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(latestRefresh)} SGT`;
  renderHealthSummary();
}

async function refreshData() {
  const button = $("#refreshData");
  button.classList.add("loading");
  button.disabled = true;
  const [marketResult, calendarResult, newsResult] = await Promise.allSettled([
    fetchJson("market"), fetchJson("calendar"), fetchJson("news")
  ]);
  let successCount = 0;
  if (marketResult.status === "fulfilled" && renderMarket(marketResult.value)) successCount += 1;
  else renderMarket({ ok: false });
  if (calendarResult.status === "fulfilled" && renderCalendar(calendarResult.value)) successCount += 1;
  else renderCalendar({ ok: false });
  if (newsResult.status === "fulfilled" && renderNews(newsResult.value)) successCount += 1;
  else renderNews({ ok: false });
  updateFreshness(successCount);
  button.classList.remove("loading");
  button.disabled = false;
}

function bindNavigation() {
  const links = $$(".primary-nav a");
  const sections = [...new Set(links.map(link => document.querySelector(link.getAttribute("href"))).filter(Boolean))];
  let navLockUntil = 0;
  links.forEach(link => link.addEventListener("click", () => {
    navLockUntil = Date.now() + 1800;
    const href = link.getAttribute("href");
    links.forEach(item => item.classList.toggle("active", item.getAttribute("href") === href));
  }));
  const observer = new IntersectionObserver(entries => {
    if (Date.now() < navLockUntil) return;
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    links.forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`));
  }, { rootMargin: "-30% 0px -60%", threshold: [0, .2, .6] });
  sections.forEach(section => observer.observe(section));
}

function bindEvents() {
  $("#refreshData").addEventListener("click", refreshData);
  $("#methodButton").addEventListener("click", () => $("#methodDialog").showModal());
  $("#feedbackButton").addEventListener("click", () => $("#feedbackDialog").showModal());
  $$('[data-close-dialog]').forEach(button => button.addEventListener("click", () => $(`#${button.dataset.closeDialog}`).close()));
  $$("#marketTabs button").forEach(button => button.addEventListener("click", () => mountChart(button.dataset.symbol)));
  $$('[data-open-symbol]').forEach(button => button.addEventListener("click", () => {
    mountChart(button.dataset.openSymbol);
    $("#markets").scrollIntoView({ behavior: "smooth" });
  }));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && latestRefresh && Date.now() - latestRefresh > 300000) refreshData();
  });
}

function init() {
  updateClocks();
  setInterval(updateClocks, 1000);
  if (WIDGETS_DISABLED) {
    $("#tickerWidget").innerHTML = '<div class="widget-placeholder compact">Live ticker paused for interface testing</div>';
    $("#tickerWidgetClone").innerHTML = '<div class="widget-placeholder compact">Live ticker paused for interface testing</div>';
    $("#marketChart").innerHTML = '<div class="widget-placeholder">Live chart paused for interface testing</div>';
    setHealth("charts", "delayed", "Paused");
  } else {
    mountTicker();
    mountChart();
  }
  bindNavigation();
  bindEvents();
  refreshData();
  setInterval(refreshData, 60000);
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    window.addEventListener("load", () => navigator.serviceWorker.register("service-worker.js").catch(() => {}));
  }
}

init();
