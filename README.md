# LiqueDT — XAUUSD market context

LiqueDT is a live, Singapore-time companion for gold-market sessions, related markets, high-impact USD events, and gold-sensitive news. It contains no trading or execution features.

## Windows app

Open `dist\LiqueDT.exe`. It launches LiqueDT in a dedicated desktop window with its own local data gateway—Python is not required. Keep the adjacent `app` folder beside the executable.

Requirements: Windows x64, the .NET 6 Desktop Runtime, and Microsoft Edge or Google Chrome. These are already available on the development machine.

To share it, send only `release\LiqueDT-Windows.zip`. The recipient must extract the entire ZIP into a new folder before running `LiqueDT.exe`; the adjacent `app` folder is required. They need Windows x64, Microsoft Edge or Chrome, the .NET 6 Desktop Runtime, and internet access for live content.

## iPhone app

The same interface is responsive and installable as a Progressive Web App. Deploy this folder to an HTTPS web host, open its URL in Safari on iPhone, tap **Share**, then **Add to Home Screen**. The Windows `.exe` cannot run on iOS; the HTTPS-hosted PWA is the iPhone version.

For GitHub Pages, follow [GITHUB-PAGES.md](GITHUB-PAGES.md). The included workflow deploys the PWA and refreshes static data snapshots every 10 minutes. GitHub Pages cannot run the live Python/.NET gateway, so snapshots are honestly marked red rather than reported as a live source.

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
- A verified Binance XAUUSDT perpetual chart and a weighted cross-market context read.
- Upcoming medium/high-impact USD calendar events.
- Gold-sensitive FXStreet headlines.
- A transparent, experimental bullish/bearish headline-narrative meter that disappears when the feed is unavailable.
- Installable/offline-capable PWA shell.
- Direct feedback delivery through FormSubmit. The inbox owner must approve FormSubmit's one-time activation message before visitor submissions are delivered.

## Which Windows file to open

During development, open `dist\LiqueDT.exe`. For a shared copy, extract `release\LiqueDT-Windows.zip` into a brand-new folder and open the `LiqueDT.exe` inside it. Do not use an older pinned shortcut or an EXE copied without its adjacent `app` folder. Version 1.7 uses a dedicated title-bar favicon, a fresh desktop browser profile, cache-busted assets, a practical-height cross-market chart, and an iPhone-specific navigation and status layout.

## Verification

```powershell
python .\verify_app.py
```

See `ARCHITECTURE.md` for the system design, provider boundaries, reliability model, security controls, and scale-up plan.

## Important boundary

LiqueDT is informational context, not financial advice or a signal service. Market hours are indicative and can vary across brokers and holidays. Headline relationships can invert and price may already reflect published information.
