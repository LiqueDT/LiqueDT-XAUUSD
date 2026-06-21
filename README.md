# LiqueDT — XAUUSD market context

LiqueDT is a live, Singapore-time companion for gold-market sessions, related markets, high-impact USD events, and gold-sensitive news. It contains no trading or execution features.

## Windows app

Open `dist\LiqueDT.exe`. It launches LiqueDT in a dedicated desktop window with its own local data gateway—Python is not required. Keep the adjacent `app` folder beside the executable.

Requirements: Windows x64, the .NET 6 Desktop Runtime, and Microsoft Edge or Google Chrome. These are already available on the development machine.

To share it, send `release\LiqueDT-Windows-v1.0.zip`. The recipient must extract the entire ZIP before running `LiqueDT.exe`; the adjacent `app` folder is required.

## iPhone app

The same interface is responsive and installable as a Progressive Web App. Deploy this folder to an HTTPS web host, open its URL in Safari on iPhone, tap **Share**, then **Add to Home Screen**. The Windows `.exe` cannot run on iOS; the HTTPS-hosted PWA is the iPhone version.

## Run it

Right-click `start.ps1` and choose **Run with PowerShell**, or run:

```powershell
python .\server.py
```

Then open `http://127.0.0.1:8765`.

The static shell can be opened directly, but live news and calendar data require the local gateway. The chart and ticker require internet access and are supplied by TradingView.

## Included

- Singapore clock and indicative XAUUSD weekly/daily open state.
- DST-aware Asia, London, and New York session countdowns displayed in Singapore time.
- Interactive XAUUSD, DXY, WTI, and U.S. 10Y charts.
- Upcoming medium/high-impact USD calendar events.
- Gold-sensitive FXStreet headlines.
- A transparent, experimental bullish/bearish headline-narrative meter that disappears when the feed is unavailable.
- Installable/offline-capable PWA shell.

## Verification

```powershell
python .\verify_app.py
```

See `ARCHITECTURE.md` for the system design, provider boundaries, reliability model, security controls, and scale-up plan.

## Important boundary

LiqueDT is informational context, not financial advice or a signal service. Market hours are indicative and can vary across brokers and holidays. Headline relationships can invert and price may already reflect published information.
