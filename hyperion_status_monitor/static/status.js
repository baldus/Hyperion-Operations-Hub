const intervalSec = window.STATUS_MONITOR_INTERVAL || 10;
const staleThresholdSec = intervalSec * 3;

const tiles = document.querySelectorAll(".tile");
const lastUpdated = document.getElementById("last-updated");
const staleBanner = document.getElementById("stale-banner");
const copyButton = document.getElementById("copy-diagnostics");

function setTile(section, status, details) {
  const tile = document.querySelector(`.tile[data-section="${section}"]`);
  if (!tile) return;
  const statusEl = tile.querySelector(".status");
  const detailsEl = tile.querySelector(".details");
  statusEl.textContent = status || "--";
  detailsEl.textContent = details || "--";
  tile.dataset.state = status || "";
}

function updateSnapshot(snapshot) {
  if (!snapshot) return;
  const generatedAt = snapshot.generated_at;
  if (generatedAt) {
    const date = new Date(generatedAt);
    lastUpdated.textContent = `Last updated: ${date.toLocaleString()}`;
    const ageSec = (Date.now() - date.getTime()) / 1000;
    staleBanner.classList.toggle("hidden", ageSec <= staleThresholdSec);
  }

  setTile("main_app", snapshot.main_app?.status, snapshot.main_app?.details);
  setTile("db", snapshot.db?.status, snapshot.db?.details);
  setTile("disk", snapshot.disk?.status, snapshot.disk?.details);
  setTile("backups", snapshot.backups?.status, snapshot.backups?.details);
  setTile("app", snapshot.app?.status, snapshot.app?.details);

  const errorCount = Array.isArray(snapshot.errors) ? snapshot.errors.length : 0;
  const errorDetails = errorCount
    ? `${errorCount} recent error(s)`
    : "No recent errors";
  setTile("errors", errorCount ? "WARN" : "OK", errorDetails);
}

async function fetchSnapshot() {
  try {
    const response = await fetch("/api/status/snapshot", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Snapshot HTTP ${response.status}`);
    }
    const data = await response.json();
    updateSnapshot(data);
  } catch (error) {
    setTile("errors", "ERROR", `Snapshot fetch failed: ${error.message}`);
  }
}

async function copyDiagnostics() {
  try {
    const response = await fetch("/api/status/diagnostics", { cache: "no-store" });
    const text = await response.text();
    await navigator.clipboard.writeText(text);
    copyButton.textContent = "Copied!";
    setTimeout(() => {
      copyButton.textContent = "Copy diagnostics";
    }, 1500);
  } catch (error) {
    copyButton.textContent = "Copy failed";
    setTimeout(() => {
      copyButton.textContent = "Copy diagnostics";
    }, 1500);
  }
}

copyButton?.addEventListener("click", copyDiagnostics);

fetchSnapshot();
setInterval(fetchSnapshot, intervalSec * 1000);
