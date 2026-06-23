# Publish LiqueDT to GitHub Pages

Use the prepared `GitHub-Pages-Upload` folder. It contains the website, iPhone/PWA icons, data normalizer, and deployment workflow. Do not upload `dist`, `desktop`, or `release` to the website repository.

## Files that must be uploaded

```text
.github/
  workflows/
    pages.yml
tools/
  build_static_data.py
.nojekyll
app-icon-192.png
app-icon-512.png
app.js
apple-touch-icon.png
favicon-32.png
favicon.ico
GITHUB-PAGES.md
icon.svg
index.html
manifest.webmanifest
README.md
requirements-pages.txt
server.py
service-worker.js
styles.css
```

The `.github` folder is essential. It tells GitHub how to preserve the last successful data, refresh the public snapshots every five minutes, and deploy the site.

## Method A: GitHub website

1. Sign in at `github.com`.
2. Click **New repository**.
3. Name it `LiqueDT` and choose **Public**. GitHub Pages on a free account works most simply with a public repository.
4. Do not add a README, `.gitignore`, or licence during repository creation.
5. Click **Create repository**.
6. On the empty repository page, click **uploading an existing file**.
7. Open `LiqueDT-App\GitHub-Pages-Upload` in Windows Explorer.
8. Select everything inside that folder—including `.github` and `tools`—and drag it onto GitHub's upload page. Upload the contents, not the outer `GitHub-Pages-Upload` folder.
9. Enter a commit message such as `Deploy LiqueDT` and click **Commit changes**.
10. In the repository, open **Settings → Pages**.
11. Under **Build and deployment → Source**, choose **GitHub Actions**.
12. Open the repository's **Actions** tab.
13. Select **Deploy LiqueDT to GitHub Pages**. If it is not already running, click **Run workflow**, select `main`, then click the green **Run workflow** button.
14. Wait for the workflow to show a green check mark.
15. Return to **Settings → Pages**. GitHub displays the published address, normally `https://YOUR-USERNAME.github.io/LiqueDT/`.

## Updating the GitHub version later

1. Replace the contents of your local `GitHub-Pages-Upload` folder with the newly prepared files.
2. In GitHub, choose **Add file → Upload files**.
3. Drag the updated contents onto the upload page and commit them to `main`.
4. The deployment workflow runs automatically.

## If market data says unavailable

1. Open **Settings → Pages** and confirm **Source** is set to **GitHub Actions**, not **Deploy from a branch**.
2. Open **Actions → Deploy LiqueDT to GitHub Pages** and confirm the latest run has a green check mark.
3. If no run exists, choose **Run workflow → main → Run workflow**.
4. Confirm that `.github/workflows/pages.yml`, `tools/build_static_data.py`, `requirements-pages.txt`, and `server.py` exist in the repository.
5. After the green deployment completes, refresh Safari. If LiqueDT was added to the Home Screen, fully close it and reopen it once so version 1.9 replaces the old cached shell.

## Install on iPhone

1. Open the published GitHub Pages address in Safari.
2. Tap the **Share** icon.
3. Scroll down and tap **Add to Home Screen**.
4. Confirm the name `LiqueDT` and tap **Add**.

## Data-status limitation

GitHub Pages is a static host and cannot run the Windows app's live `/api/*` gateway. The TradingView ticker and charts remain live in the browser, while GitHub Actions refreshes market, news, and calendar snapshots every five minutes and preserves the last successful snapshot if an upstream source temporarily fails. LiqueDT marks these snapshots red rather than pretending they are a continuously live API. Version 1.9 also shows when each snapshot was created, when the source last refreshed successfully, and how old the latest news headline is.

For continuously live green API status, keep GitHub as the source repository and deploy to a server-capable host such as Cloudflare Pages Functions, Netlify, Vercel, or a VPS.
