# Marketplace Monitor

Marketplace Monitor checks saved Facebook Marketplace search pages for new listing IDs, applies local title and price rules, and sends a phone notification containing the listing link. It keeps the Facebook session and listing history on your computer.

## What the first version does

- Reuses a Facebook login stored in a local Playwright browser profile.
- Supports multiple Marketplace search URLs.
- Uses the location, radius, condition, sort order, and category already encoded in each Marketplace URL.
- Applies additional include, exclude, minimum-price, and maximum-price rules locally.
- Remembers listing IDs in SQLite so the same listing is not repeatedly announced.
- Sends push notifications through [ntfy](https://ntfy.sh) or prints matches to the console.
- Establishes a silent baseline on the first successful run by default.

This is a read-only monitor. It does not message sellers, place orders, or attempt to bypass login challenges.

## Windows setup

Open PowerShell in the repository and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
playwright install chromium
Copy-Item config.example.yaml config.yaml
```

If PowerShell blocks virtual-environment activation, run this once in the current terminal:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

## Configure searches

For each product:

1. Open Facebook Marketplace normally.
2. Search for the product.
3. Set location, travel radius, price, condition, category, and newest-first sorting in Facebook.
4. Copy the resulting URL into `config.yaml`.
5. Add title terms and optional local price limits.

Example:

```yaml
searches:
  - name: Flair 58 Plus
    url: https://www.facebook.com/marketplace/search/?query=flair%2058
    min_price: 250
    max_price: 550
    include_any:
      - flair 58
      - flair58
    exclude:
      - wanted
      - looking for
```

Facebook remains responsible for distance and condition filtering because those values are more reliable in the Marketplace search controls than in listing-card text.

## Configure phone notifications

Install the ntfy mobile app, choose a long random topic name, and subscribe to it. Put the same topic in `config.yaml`:

```yaml
notifications:
  provider: ntfy
  ntfy:
    server: https://ntfy.sh
    topic: replace-with-a-long-random-topic
```

Public ntfy topics are accessible to anyone who knows the topic name, so use an unguessable value. For an authenticated ntfy topic, set the token only in the local environment:

```powershell
$env:NTFY_ACCESS_TOKEN = "your-token"
```

Use `provider: console` while testing if phone notifications are not configured yet.

## Save the Facebook session

Run:

```powershell
marketplace-monitor login
```

A browser window opens. Log in manually, complete any two-factor prompt, wait until Marketplace is visible, and then press Enter in PowerShell. Credentials are not placed in the configuration file. The resulting `browser-profile` directory is local and ignored by Git.

## Run it

Test one check:

```powershell
marketplace-monitor run-once
```

The first successful check records the current listings without sending a burst of old results. Start continuous monitoring with:

```powershell
marketplace-monitor watch
```

The check interval comes from `check_interval_minutes` in `config.yaml`. The computer must remain awake and connected to the internet while the monitor is running.

## Development checks

```powershell
pytest
python -m compileall src
```

The automated tests are offline and do not access Facebook.

## Repository safety

The committed `.gitignore` excludes:

- `config.yaml`
- `.env`
- `browser-profile/`
- `data/` and SQLite databases
- logs and virtual environments

Do not override these exclusions for session or credential files. Source code can remain public without exposing the local Facebook session.

## Platform limitation

Facebook does not provide a general consumer Marketplace monitoring API. Browser automation can break when Facebook changes its interface and may trigger login checks or account restrictions. Meta's terms restrict automated data collection without permission. Keep checks infrequent and do not use this project to bypass technical restrictions.
