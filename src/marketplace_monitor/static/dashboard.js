const relativeFormatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

function updateRelativeTimes() {
  document.querySelectorAll("[data-relative-time]").forEach((element) => {
    const timestamp = Date.parse(element.dateTime);
    if (Number.isNaN(timestamp)) return;
    const seconds = Math.round((timestamp - Date.now()) / 1000);
    const intervals = [
      ["day", 86400],
      ["hour", 3600],
      ["minute", 60],
    ];
    const [unit, divisor] =
      intervals.find(([, size]) => Math.abs(seconds) >= size) || ["second", 1];
    element.textContent = relativeFormatter.format(Math.round(seconds / divisor), unit);
    element.title = new Date(timestamp).toLocaleString();
  });
}

async function checkForUpdates() {
  if (!document.body.hasAttribute("data-latest-run")) return;
  if (document.visibilityState !== "visible") return;
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) return;
    const status = await response.json();
    const knownRun = document.body.dataset.latestRun || "";
    const latestRun = status.latest_run?.completed_utc || "";
    const knownDashboardUpdate = document.body.dataset.dashboardUpdated || "";
    const latestDashboardUpdate = status.dashboard_updated_utc || "";
    const knownCounts = document.body.dataset.counts || "";
    const knownStaleState = document.body.dataset.monitorStale || "false";
    const latestCounts = [
      status.counts?.active || 0,
      status.counts?.interested || 0,
      status.counts?.dismissed || 0,
    ].join(":");
    if (
      (latestRun && latestRun !== knownRun) ||
      (latestDashboardUpdate && latestDashboardUpdate !== knownDashboardUpdate) ||
      latestCounts !== knownCounts ||
      String(Boolean(status.monitor_stale)) !== knownStaleState
    ) {
      window.location.reload();
    }
  } catch (_error) {
    // Keep the last useful dashboard visible during temporary network loss.
  }
}

document.querySelectorAll("[data-submit-on-change]").forEach((control) => {
  control.addEventListener("change", () => control.form.submit());
});
document.querySelectorAll("[data-refresh]").forEach((control) => {
  control.addEventListener("click", () => window.location.reload());
});
document.querySelectorAll("[data-add-suggestions]").forEach((control) => {
  control.addEventListener("click", () => {
    const target = document.querySelector("[data-exact-phrases]");
    if (!target) return;
    const existing = target.value
      .split(/[,\n]+/)
      .map((phrase) => phrase.trim())
      .filter(Boolean);
    const known = new Set(existing.map((phrase) => phrase.toLocaleLowerCase()));
    document.querySelectorAll("[data-phrase-suggestion]:checked").forEach((checkbox) => {
      const phrase = checkbox.value.trim();
      if (phrase && !known.has(phrase.toLocaleLowerCase())) {
        existing.push(phrase);
        known.add(phrase.toLocaleLowerCase());
      }
      checkbox.checked = false;
    });
    target.value = existing.join("\n");
    target.focus();
  });
});
document.querySelectorAll(".search-form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (event.submitter?.value !== "suggest") return;
    event.submitter.textContent = "Analyzing…";
    form.setAttribute("aria-busy", "true");
  });
});
document.addEventListener("visibilitychange", checkForUpdates);
updateRelativeTimes();
setInterval(updateRelativeTimes, 30_000);
setInterval(checkForUpdates, 30_000);

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/service-worker.js").catch(() => {}),
  );
}
