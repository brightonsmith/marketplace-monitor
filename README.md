# Marketplace Monitor

Marketplace Monitor watches saved Facebook Marketplace searches, applies local
title and price rules, and sends phone notifications for newly listed matches.
It keeps the Facebook session and listing history on the machine running it.

The monitor is read-only. It does not message sellers, place orders, or bypass
Facebook login challenges.

## Command model

The public CLI has one command for each job:

```text
marketmon init                         create the configuration
marketmon login                        save and verify a Facebook session
marketmon add [SEARCH.yaml]             add a search interactively or from YAML
marketmon list                          list active searches
marketmon remove "SEARCH NAME"          remove a search
marketmon feedback LISTING_ID STATE      save interested/dismissed feedback
marketmon check [-n COUNT] [-s NAME]    inspect results without notifications/history changes
marketmon dashboard                     serve the local results dashboard
marketmon watch [--once]                run real monitoring cycles
marketmon service ACTION                manage autonomous Linux operation
```

`check` never changes notification history or sends listing alerts. It only
refreshes the separate dashboard snapshot. `watch --once`
performs one real cycle, including baselining, deduplication, quiet hours, and
eligible notifications. `watch` repeats those cycles continuously.

`check` refreshes a separate dashboard snapshot containing candidate titles,
prices, images, locations, distances, and ranking details. This snapshot never
participates in notification deduplication.

Launch the dashboard after a check:

```bash
marketmon check
marketmon dashboard
```

Open `http://localhost:8000`. To reach it through Tailscale, bind it explicitly
with `marketmon dashboard --host 0.0.0.0` and open the Pi's Tailscale hostname,
for example `http://argus:8000`. The installed dashboard service uses this bind
address automatically.

The dashboard is a self-contained mobile web app: it does not depend on a CSS or
JavaScript CDN, checks for completed monitoring runs every 30 seconds, and
reloads when newer results are available. It includes current, saved, and
dismissed views, per-search result limits, persistent feedback controls, and
monitoring history. Selecting **Interested** saves the listing before opening
the Facebook listing. Saved listings remain in the dashboard when they disappear
from the latest search and are updated if they reappear with changed details.

For an HTTPS home-screen app on iPhone, use Tailscale Serve as a private reverse
proxy after the dashboard is running:

```bash
tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
```

Open the HTTPS URL reported by `tailscale serve status` in Safari, then use
**Share > Add to Home Screen**. Set that URL in
`~/.config/marketmon/environment` so notification links use it as well:

```text
MARKETMON_DASHBOARD_URL=https://argus.your-tailnet.ts.net
```

Apply the environment change with `marketmon service restart`. Tailscale Serve
keeps the HTTPS endpoint restricted to devices authorized on the tailnet; no
router port forwarding is required.

`-c/--config` may appear before or after a top-level command. Relative browser
profile and database paths are resolved from the configuration file's directory.

## Raspberry Pi quick start

No repository clone is required for normal use. Install the released package in
a virtual environment:

```bash
sudo apt update
sudo apt install -y git python3-venv
python3 -m venv ~/.venvs/marketmon
source ~/.venvs/marketmon/bin/activate
python -m pip install --upgrade pip
python -m pip install "marketmon==0.3.1"
python -m playwright install --with-deps chromium
```

Create a durable configuration outside a cloned repository:

```bash
marketmon init
```

`init` opens a numbered editor showing all monitoring, notification, browser,
and storage settings. Select settings in any order, then choose `S` to validate
and save. Use `Q` to cancel without creating a file. For an unattended install
that should use every default, run `marketmon init --defaults`.

When no local `config.yaml` exists, Marketmon defaults to
`~/.config/marketmon/config.yaml`. An existing local config remains supported.
Set `MARKETMON_CONFIG` or pass `-c/--config` to choose another path.

For phone notifications, set the provider and ntfy topic:

```yaml
notifications:
  provider: ntfy
  ntfy:
    server: https://ntfy.sh
    topic: replace-with-a-long-random-topic
```

Install the ntfy phone app and subscribe to the same topic. Public ntfy topics
are accessible to anyone who knows the name, so use a long random value. For an
authenticated topic, put the token in the service environment as
`NTFY_ACCESS_TOKEN`; never commit it. The installed service optionally reads
`~/.config/marketmon/environment`, which can contain:

```text
NTFY_ACCESS_TOKEN=your-token
```

Protect it with `chmod 600 ~/.config/marketmon/environment`.

### Save the Facebook login

Run this from a terminal inside the Pi's graphical desktop:

```bash
marketmon login
```

Playwright opens its bundled Chromium. Log in, complete any two-factor or
checkpoint prompt, wait until Marketplace is visible, then press Enter in the
terminal. Marketmon verifies the session before saving it.

A headless SSH terminal has no display and cannot open the login browser. On a Pi
without a connected monitor, use VNC to reach the Pi desktop, open its Terminal
application, and run the command there. Routine monitoring is headless; VNC is
only needed for login and later reauthentication.

### Add and inspect a search

In Facebook Marketplace, search for the product and configure location, radius,
condition, price, category, and sorting. Copy the complete results URL, then run:

```bash
marketmon add
```

The command opens a numbered editor showing the name, URL, optional local price
bounds, exact title phrases, exclusions, relevance threshold, and an optional
hard radius. Select fields
in any order, then choose `S` to validate and save. Title matching is
case-insensitive. A short distinctive phrase such as `flair 58` also matches
longer titles such as `Flair 58 Plus Espresso Maker`.

Inspect the active searches and current results:

```bash
marketmon list
marketmon check
marketmon check -s "Flair 58 Plus" -n 5
```

`check` is read-only, making it safe for login verification and filter tuning.
With no active searches, it verifies the saved Facebook session directly.
When the output looks correct, exercise one real monitoring cycle:

```bash
marketmon watch --once
```

The first successful real cycle silently records existing listings by default.
Later cycles alert only for new matching listing IDs.

Facebook occasionally mixes distant local-pickup results into a radius-limited
search. Set `max_distance_miles` to enforce the radius independently:

```yaml
max_distance_miles: 40
```

Marketmon reads the selected search center from Facebook's location control, so
the origin is not duplicated in configuration. It geocodes Facebook's displayed
listing city and excludes unresolved or out-of-radius listings. Distance is
therefore city-center approximate. Title, price, and exclusion checks happen
before geocoding, and successful lookups are cached in SQLite. An interactive
`marketmon check` is limited to one uncached lookup per second; autonomous
monitoring uses the stricter four-lookups-per-minute limit for regular scripts.
Geocoding data is © OpenStreetMap contributors and is subject to the Nominatim
usage policy.
Set `MARKETMON_GEOCODER_DOMAIN` to switch to a compatible Nominatim provider or
self-hosted instance without changing Marketmon.

Save feedback for any previously seen listing using the ID printed by `check`:

```bash
marketmon feedback 123456789 dismissed
marketmon feedback 123456789 interested
marketmon feedback 123456789 clear
```

Dismissed listings are removed from future status summaries and reports. Reports
are grouped by search, and `check -n 5` shows up to five results for each search.

### Run autonomously

Marketmon can install a user-level systemd service using the current Python
environment and configuration:

```bash
sudo loginctl enable-linger "$USER"
marketmon service install
marketmon service status
```

Linger lets the user service start at boot without an SSH or desktop login. The
service starts immediately, restarts after crashes, and starts again after a
normal reboot or power restoration. Temporary internet failures are retried by
the next monitoring cycle.

Service controls:

```bash
marketmon service status
marketmon service logs
marketmon service logs --follow
marketmon service restart
marketmon service uninstall
```

Changing searches does not require a restart because `watch` reloads the config
before every cycle. Restart after changing browser, notification, interval, or
service environment settings.

If an older system-wide `/etc/systemd/system/marketmon.service` is already
running, stop and disable it before installing the user service. Marketmon blocks
installation when it detects the system-wide service, preventing two monitors
from sending duplicate notifications.

## Search YAML import

Interactive `add` is the normal path. For backups, scripts, or remote deployment,
`add` also accepts one search mapping or a full configuration containing a
`searches` list:

```yaml
name: Flair 58 Plus
url: "https://www.facebook.com/marketplace/denver/search?query=flair%2058"
min_price: 200
max_price: 550
minimum_relevance: 0.20
max_distance_miles: 40
include_any:
  - flair 58
  - flair58
exclude:
  - wanted
  - looking for
  - broken
  - for parts
```

```bash
marketmon add flair.yaml -c ~/.config/marketmon/config.yaml
marketmon add flair.yaml --replace -c ~/.config/marketmon/config.yaml
```

Search names are unique and case-insensitive. `marketmon list --json` provides
stable machine-readable output.

## Notifications and failure behavior

- Matching listing alerts use the configured provider.
- `notify_on_startup: true` sends a startup summary after the first successful
  cycle. Startup notifications bypass quiet hours because they confirm that the
  process actually started.
- Status heartbeats honor `status_interval_minutes` and quiet hours.
- Matching listings found during quiet hours remain pending in SQLite and are
  delivered after quiet hours end.
- An expired Facebook session, login redirect, checkpoint, or two-factor prompt
  sends one high-priority operational alert through the configured provider.
  Monitoring continues retrying, but the alert is not repeated every cycle. Run
  `marketmon login` from the graphical desktop to restore the session.

Example quiet hours:

```yaml
quiet_hours:
  start: "22:00"
  end: "07:00"
```

Remove `quiet_hours` or set it to `null` to disable the quiet period.

## Updating

Activate the same virtual environment, upgrade from PyPI, and restart:

```bash
source ~/.venvs/marketmon/bin/activate
python -m pip install --upgrade marketmon
python -m playwright install chromium
marketmon service restart
```

Installations of version 0.3.0 used the old distribution name. Migrate those
once before installing a PyPI release:

```bash
python -m pip uninstall -y marketplace-monitor
python -m pip install "marketmon==0.3.1"
```

## Development

Clone only when modifying the source:

```bash
git clone https://github.com/brightonsmith/marketplace-monitor.git
cd marketplace-monitor
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m playwright install chromium
pytest
python -m compileall -q src
```

Local configuration, browser profiles, SQLite data, environment files, and logs
are excluded from Git. Do not commit session or notification credentials.

## Platform limitation

Facebook does not provide a general consumer Marketplace monitoring API. Browser
automation can break when Facebook changes its interface and may trigger login
checks or account restrictions. Keep checks infrequent and do not use this project
to bypass technical restrictions.
