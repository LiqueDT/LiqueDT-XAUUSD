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
  "TVC:USOIL": { name: "WTI Crude Oil", tag: "ENERGY" },
  "OANDA:USB10YUSD": { name: "U.S. 10Y Bond Yield", tag: "RATES · OANDA" }
};

let activeSymbol = "OANDA:XAUUSD";
let latestRefresh = null;
const WIDGETS_DISABLED = new URLSearchParams(location.search).has("no-widgets");
const healthState = { charts: "checking", news: "checking", calendar: "checking" };

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
    detail = "Connecting charts, news and calendar";
  } else if (values.includes("offline")) {
    state = "offline";
    title = "Some data is unavailable";
    detail = "Unavailable sources are clearly marked below";
  } else if (values.includes("delayed")) {
    state = "delayed";
    title = "Live with backup data";
    detail = "A cached or backup source is currently active";
  }
  dot.className = `health-dot ${state}`;
  summary.textContent = title;
  $("#dataFreshness").textContent = detail;
}

function setHealth(key, state, label) {
  healthState[key] = state;
  const element = $(`#${key}Health`);
  element.className = state;
  element.textContent = label || ({ live: "Live", delayed: "Backup", offline: "Offline", checking: "Checking" }[state]);
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
  mountWidget($("#tickerWidget"), "embed-widget-ticker-tape.js", {
    symbols: [
      { proName: "OANDA:XAUUSD", title: "Gold" },
      { proName: "CAPITALCOM:DXY", title: "Dollar index" },
      { proName: "OANDA:USB10YUSD", title: "U.S. 10Y" },
      { proName: "TVC:USOIL", title: "WTI crude" },
      { proName: "OANDA:XAGUSD", title: "Silver" }
    ],
    showSymbolLogo: true, isTransparent: true, displayMode: "adaptive", colorTheme: "dark", locale: "en"
  });
}

function mountChart(symbol = activeSymbol) {
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
    autosize: true, symbol, interval: "15", timezone: "Asia/Singapore", theme: "dark",
    style: "1", locale: "en", backgroundColor: "rgba(16, 23, 31, 1)",
    gridColor: "rgba(226, 232, 240, 0.055)", hide_top_toolbar: false,
    allow_symbol_change: false, save_image: false, calendar: false, support_host: "https://www.tradingview.com"
  }, "charts");
}

async function fetchJson(path) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 9000);
  try {
    const response = await fetch(path, { headers: { Accept: "application/json" }, signal: controller.signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
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

function renderCalendar(payload) {
  const status = $("#calendarStatus");
  if (!payload.ok || !payload.events?.length) {
    if (!WIDGETS_DISABLED) {
      status.className = "source-status delayed";
      status.textContent = "LIVE BACKUP";
      setHealth("calendar", "delayed", "Backup");
      mountWidget($("#calendarList"), "embed-widget-events.js", {
        colorTheme: "dark", isTransparent: true, width: "100%", height: 385,
        locale: "en", importanceFilter: "0,1", countryFilter: "us"
      });
    } else {
      status.className = "source-status offline";
      status.textContent = "UNAVAILABLE";
      setHealth("calendar", "offline", "Unavailable");
      $("#calendarList").innerHTML = '<div class="empty-feed">The calendar feed is unavailable right now. Use the full calendar link below before making time-sensitive decisions.</div>';
    }
    return false;
  }
  status.className = `source-status ${payload.stale ? "delayed" : "live"}`;
  status.textContent = payload.stale ? "CACHED" : "LIVE FEED";
  setHealth("calendar", payload.stale ? "delayed" : "live", payload.stale ? "Cached" : "Live");
  $("#calendarList").innerHTML = payload.events.slice(0, 7).map(event => {
    const time = event.time_utc
      ? formatter(SINGAPORE_TZ, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(event.time_utc))
      : "TBC";
    const values = [event.forecast && `Fcst ${event.forecast}`, event.previous && `Prev ${event.previous}`].filter(Boolean).join(" · ") || "Details pending";
    return `<article class="calendar-item">
      <div class="calendar-time"><strong>${escapeHtml(time)}</strong><small>${escapeHtml(eventDay(event.time_utc))}</small></div>
      <div class="calendar-copy"><h3>${escapeHtml(event.title)}</h3><p>USD · ${escapeHtml(values)}</p></div>
      <span class="impact ${event.impact === "High" ? "high" : "medium"}"><i></i>${escapeHtml(event.impact)}</span>
    </article>`;
  }).join("");
  return true;
}

function renderNews(payload) {
  const status = $("#newsStatus");
  if (!payload.ok || !payload.items?.length) {
    if (!WIDGETS_DISABLED) {
      status.className = "source-status delayed";
      status.textContent = "LIVE BACKUP";
      setHealth("news", "delayed", "Backup");
      mountWidget($("#newsList"), "embed-widget-timeline.js", {
        feedMode: "symbol", symbol: "OANDA:XAUUSD", colorTheme: "dark",
        isTransparent: true, displayMode: "regular", width: "100%", height: 385, locale: "en"
      });
    } else {
      status.className = "source-status offline";
      status.textContent = "UNAVAILABLE";
      setHealth("news", "offline", "Unavailable");
      $("#newsList").innerHTML = '<div class="empty-feed">Live headlines could not be reached. LiqueDT will retry automatically; open the source link below for a direct check.</div>';
    }
    renderPulse(null);
    return false;
  }
  status.className = `source-status ${payload.stale ? "delayed" : "live"}`;
  status.textContent = payload.stale ? "CACHED" : "LIVE FEED";
  setHealth("news", payload.stale ? "delayed" : "live", payload.stale ? "Cached" : "Live");
  $("#newsList").innerHTML = payload.items.slice(0, 7).map(item => `<a class="news-item" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">
    <span class="news-effect ${escapeHtml(item.impact)}">${escapeHtml(item.impact || "mixed")}</span>
    <span class="news-copy"><h3>${escapeHtml(item.title)}</h3><p><span>${escapeHtml(item.source || "FXStreet")}</span><span>·</span><span>${escapeHtml(relativeTime(item.published))}</span></p></span>
  </a>`).join("");
  renderPulse(payload.pulse);
  return true;
}

function renderPulse(pulse) {
  const status = $("#pulseStatus");
  if (!pulse) {
    status.className = "source-status offline";
    status.textContent = "NO FEED";
    $("#pulseTitle").textContent = "Narrative read unavailable";
    $("#pulseSummary").textContent = "Live headlines are not reachable. No directional assumption is shown when the source cannot be verified.";
    $("#pulseNeedle").style.left = "50%";
    return;
  }
  const score = Math.max(-1, Math.min(1, Number(pulse.score) || 0));
  status.className = "source-status live";
  status.textContent = `${pulse.sample_size || 0} HEADLINES`;
  $("#pulseNeedle").style.left = `${50 + score * 42}%`;
  $("#pulseTitle").textContent = pulse.title || "Balanced narrative";
  $("#pulseSummary").textContent = pulse.summary || "Recent headlines contain mixed gold-sensitive language.";
  const factors = pulse.factors?.length ? pulse.factors : ["Dollar", "Rates", "Risk"];
  $("#pulseFactors").innerHTML = factors.slice(0, 4).map(factor => `<span>${escapeHtml(factor)}</span>`).join("");
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
  const [calendarResult, newsResult] = await Promise.allSettled([
    fetchJson("/api/calendar"), fetchJson("/api/news")
  ]);
  let successCount = 0;
  if (calendarResult.status === "fulfilled" && renderCalendar(calendarResult.value)) successCount += 1;
  else renderCalendar({ ok: false });
  if (newsResult.status === "fulfilled" && renderNews(newsResult.value)) successCount += 1;
  else renderNews({ ok: false });
  updateFreshness(successCount);
  button.classList.remove("loading");
  button.disabled = false;
}

function bindNavigation() {
  const links = $$(".desktop-nav a");
  const sections = links.map(link => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  const observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
    if (!visible) return;
    links.forEach(link => link.classList.toggle("active", link.getAttribute("href") === `#${visible.target.id}`));
  }, { rootMargin: "-30% 0px -60%", threshold: [0, .2, .6] });
  sections.forEach(section => observer.observe(section));
}

function bindEvents() {
  $("#refreshData").addEventListener("click", refreshData);
  $("#methodButton").addEventListener("click", () => $("#methodDialog").showModal());
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
    $("#marketChart").innerHTML = '<div class="widget-placeholder">Live chart paused for interface testing</div>';
    setHealth("charts", "delayed", "Paused");
  } else {
    mountTicker();
    mountChart();
  }
  bindNavigation();
  bindEvents();
  refreshData();
  setInterval(refreshData, 300000);
  if ("serviceWorker" in navigator && location.protocol.startsWith("http")) {
    window.addEventListener("load", () => navigator.serviceWorker.register("service-worker.js").catch(() => {}));
  }
}

init();
