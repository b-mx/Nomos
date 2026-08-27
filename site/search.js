const INDEX_URL = "index/aliases.json";

async function loadIndex() {
  const statusEl = document.getElementById("status");
  try {
    const response = await fetch(INDEX_URL);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    statusEl.textContent = `Loaded ${data.entries.length} entries (generated ${data.generated_at}).`;
    return data.entries;
  } catch (err) {
    statusEl.textContent = `Failed to load index: ${err.message}`;
    return [];
  }
}

function buildSearchRecords(entries) {
  return entries.map((entry) => ({
    entry,
    name: entry.name,
    aliasValues: (entry.aliases || []).map((a) => a.value),
  }));
}

function renderResults(records) {
  const list = document.getElementById("results");
  list.innerHTML = "";
  for (const { entry } of records) {
    const li = document.createElement("li");
    li.className = "result";

    const title =
      entry.canonical_type === "product"
        ? `${entry.name} (${entry.vendor_id}/${entry.product_id})`
        : `${entry.name} (${entry.vendor_id})`;

    const tagsHtml = (entry.tags || []).map((t) => `<span>${t}</span>`).join("");
    const servicesHtml = (entry.services || [])
      .map((s) => `<span>${s.name} ${s.protocol}/${s.port}</span>`)
      .join("");

    const aliasesBySource = {};
    for (const alias of entry.aliases || []) {
      (aliasesBySource[alias.source] ||= []).push(alias);
    }
    const aliasesHtml = Object.entries(aliasesBySource)
      .map(([source, aliases]) => {
        const values = aliases
          .map((a) => `${a.value}${a.ecosystem ? ` (${a.ecosystem})` : ""} — ${a.confidence}`)
          .join(", ");
        return `<dt>${source}</dt><dd>${values}</dd>`;
      })
      .join("");

    li.innerHTML = `
      <h2>${title}</h2>
      <div class="meta">${entry.canonical_type}${entry.type ? ` · ${entry.type}` : ""}</div>
      <div class="tags">${tagsHtml}</div>
      <div class="services">${servicesHtml}</div>
      <dl class="aliases">${aliasesHtml}</dl>
    `;
    list.appendChild(li);
  }
}

async function main() {
  const entries = await loadIndex();
  const records = buildSearchRecords(entries);

  const fuse = new Fuse(records, {
    keys: ["name", "aliasValues"],
    threshold: 0.35,
    ignoreLocation: true,
  });

  renderResults(records.slice(0, 20));

  const searchBox = document.getElementById("search-box");
  searchBox.addEventListener("input", () => {
    const query = searchBox.value.trim();
    if (!query) {
      renderResults(records.slice(0, 20));
      return;
    }
    const results = fuse.search(query).map((r) => r.item);
    renderResults(results.slice(0, 30));
  });
}

main();
