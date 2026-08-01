# Marketplace Monitor

Marketplace Monitor checks saved Facebook Marketplace search pages for new listing IDs, applies local title and price rules, and sends a phone notification containing the listing link. It keeps the Facebook session and listing history on your computer.

## What it does

- Reuses a Facebook login stored in a local Playwright browser profile.
- Supports multiple Marketplace search URLs.
- Adds, replaces, lists, and removes searches without stopping a running watcher.
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
marketmon init
```

The environment installs the project in editable mode, so local source changes are immediately available without reinstalling the package. To update an existing environment after dependency changes, run:

```powershell
conda env update -f environment.yml --prune
conda activate marketplace-monitor
```

The project is also a standard Python package:

```powershell
python -m pip install .
playwright install chromium
marketmon init
```

`marketmon` is the primary command. The original `marketplace-monitor` command
remains available as a compatibility alias.

## Configure searches

Each configuration can contain any number of searches. For each product:

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
    minimum_relevance: 0.20
    include_any:
      - flair 58
      - flair58
    exclude:
      - wanted
      - looking for
```

Facebook remains responsible for distance and condition filtering because those values are more reliable in the Marketplace search controls than in listing-card text.

You can edit the main YAML directly, or keep each product in a small transferable
file:

```powershell
marketmon template search
marketmon template search -o flair.yaml
```

The first command prints the template to the console. The second writes the same
template to `flair.yaml`. Then edit the generated file:

```yaml
name: Flair 58 Plus
url: https://www.facebook.com/marketplace/denver/search?query=flair%2058
max_price: 500
minimum_relevance: 0.20
include_any:
  - flair 58 plus
  - flair 58+
exclude:
  - wanted
  - broken
```

Activate it with:

```powershell
marketmon add flair.yaml
```

The `add` command also accepts a complete configuration containing multiple
entries under `searches`. Search names are unique and case-insensitive. Use
`--replace` to update an existing search with the same name.

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
marketmon login
```

A browser window opens. Log in manually, complete any two-factor prompt, wait until Marketplace is visible, and then press Enter in PowerShell. Credentials are not placed in the configuration file. The resulting `browser-profile` directory is local and ignored by Git.

## Run it

Test one check:

```powershell
marketmon check
```

The first successful check records the current listings without sending a burst of old results. Start continuous monitoring with:

```powershell
marketmon watch
```

When `notify_on_startup: true` (the default), `watch` sends one concise phone
summary after its first successful check. This is separate from
`notify_on_first_run`, which controls individual alerts for listings that already
exist when the database is first created.

The check interval comes from `check_interval_minutes` in `config.yaml`. The computer must remain awake and connected to the internet while the monitor is running.

## CLI reference

```text
marketmon init [-c PATH] [--force]
marketmon template config [-o PATH] [--force]
marketmon template search [-o PATH] [--force]
marketmon login [-c PATH]
marketmon check [-c PATH]
marketmon report [-c PATH] [-n COUNT] [-s "SEARCH NAME"]
marketmon watch [-c PATH]
marketmon add SEARCH.yaml [-c PATH] [--replace]
marketmon list [-c PATH] [--json]
marketmon remove "SEARCH NAME" [-c PATH]
```

`-c` and `--config` may appear before or after the subcommand. Relative browser
profile and database paths are resolved from the selected configuration file,
not from the shell's current directory.

The watcher reloads the configuration before every check. A search added by
another terminal becomes active on the next interval without restarting the
process. Each newly added search establishes its own silent baseline when
`notify_on_first_run` is false. Removing a search also cancels any quiet-hours
notifications still pending for it.

`marketmon list --json` provides stable machine-readable output for scripts and
remote administration.

`marketmon report` performs a fresh check without changing notification history
and prints the best current listings directly to the console. Each entry includes
semantic title-match percentage, the combined relevance/price score, exact or
candidate status, price, title, location, configured search name, and URL:

```powershell
marketmon report -n 10
marketmon report -n 5 -c C:\monitor\config.yaml
marketmon report -s "Flair 58 Plus" -n 10
marketmon report -s "Flair 58 Plus" -s "Spider Putter" -n 10
```

Reports include all active searches by default. `-s/--search` selects an exact
search name case-insensitively and may be repeated to report on several products.

## Updating a Raspberry Pi remotely

A long-running Pi service can use an explicit managed configuration:

```bash
marketmon watch -c /home/pi/.config/marketmon/config.yaml
```

From Windows, transfer a new product definition and activate it over SSH:

```powershell
scp .\flair.yaml pi@10.0.0.61:/tmp/flair.yaml
ssh pi@10.0.0.61 "marketmon add /tmp/flair.yaml -c /home/pi/.config/marketmon/config.yaml"
ssh pi@10.0.0.61 "marketmon list -c /home/pi/.config/marketmon/config.yaml"
```

No service restart is needed. Ten searches are supported; they are loaded in one
browser session and checked sequentially during each interval.

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
clears the search's `minimum_relevance` threshold, the status reports that no
relevant candidate was found. The default is `0.20`; raise it to suppress weak
fallback suggestions or lower it to allow broader ones. This threshold affects
only closest-match status summaries, not listings that pass the normal filters.
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
