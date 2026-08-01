# Marketplace Monitor

Marketplace Monitor checks saved Facebook Marketplace search pages for new listing IDs, applies local title and price rules, and sends a phone notification containing the listing link. It keeps the Facebook session and listing history on your computer.

## What the first version does

- Reuses a Facebook login stored in a local Playwright browser profile.
- Supports multiple Marketplace search URLs.
- Uses the location, radius, condition, sort order, and category already encoded in each Marketplace URL.
- Applies additional include, exclude, minimum-price, and maximum-price rules locally.
- Remembers listing IDs in SQLite so the same listing is not repeatedly announced.
- Sends push notifications through [ntfy](https://ntfy.sh) or prints matches to the console.
- Sends an hourly status heartbeat when no listing alerts arrive, including the best current match.
- Establishes a silent baseline on the first successful run by default.

This is a read-only monitor. It does not message sellers, place orders, or attempt to bypass login challenges.

## Windows setup with Conda

Open Anaconda Prompt or a PowerShell terminal with Conda initialized, then run:

```powershell
conda env create -f environment.yml
conda activate marketplace-monitor
playwright install chromium
Copy-Item config.example.yaml config.yaml
```

The environment installs the project in editable mode, so local source changes are immediately available without reinstalling the package. To update an existing environment after dependency changes, run:

```powershell
conda env update -f environment.yml --prune
conda activate marketplace-monitor
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

When `notify_on_startup: true` (the default), `watch` sends one concise phone
summary after its first successful check. This is separate from
`notify_on_first_run`, which controls individual alerts for listings that already
exist when the database is first created.

The check interval comes from `check_interval_minutes` in `config.yaml`. The computer must remain awake and connected to the internet while the monitor is running.

By default, `watch` also sends a status notification after 60 minutes without any
notification. If listings pass all filters, the status shows the highest-relevance
match, with lower price breaking close or equal scores. Otherwise it shows the
highest-scoring candidate from the latest check. Ranking minimizes a weighted
loss: 90% title distance and 10% price noncompliance. Title similarity combines
40% word unigram/bigram TF-IDF cosine similarity with 60% character 3–5-gram
TF-IDF cosine similarity. The query vectors use the search name, Marketplace
query, and every `include_any` phrase; repeated alias features are deduplicated.
IDF is calculated from the current fetched listings, so rare, informative terms
receive more weight without hardcoding any product, brand, or model. If nothing
clears a minimum relevance threshold, the status reports that no relevant
candidate was found.
The timer resets after either a listing alert or a status message is successfully
sent. Configure or disable it in `config.yaml`:

```yaml
status_interval_minutes: 60  # use 0 to disable
```

To avoid overnight interruptions, add quiet hours using the computer's local
time. Both listing alerts and status heartbeats are held during this window.
Matching listings remain pending in SQLite and are sent on the first check after
quiet hours end. Overnight ranges are supported:

```yaml
quiet_hours:
  start: "22:00"
  end: "07:00"
```

Remove `quiet_hours` entirely if you do not want a quiet period.

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
- logs and local environment directories

Do not override these exclusions for session or credential files. Source code can remain public without exposing the local Facebook session.

## Platform limitation

Facebook does not provide a general consumer Marketplace monitoring API. Browser automation can break when Facebook changes its interface and may trigger login checks or account restrictions. Meta's terms restrict automated data collection without permission. Keep checks infrequent and do not use this project to bypass technical restrictions.
