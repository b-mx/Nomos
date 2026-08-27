// Coverage numbers come from index/stats.json — a tiny file with just
// counts, not the full flattened index (which doesn't scale to fetch just
// to render three numbers on the landing page).
async function loadCoverage() {
  const el = document.getElementById("coverage");
  try {
    const response = await fetch("index/stats.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const stats = await response.json();
    el.textContent = `${stats.vendor_count} vendors · ${stats.product_count} products · ${stats.sources.length} sources`;
  } catch (err) {
    el.textContent = `Coverage unavailable: ${err.message}`;
  }
}

loadCoverage();
