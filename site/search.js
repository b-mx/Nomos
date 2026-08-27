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

    const h2 = document.createElement("h2");
    h2.textContent = title;
    li.appendChild(h2);

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${entry.canonical_type}${entry.type ? ` · ${entry.type}` : ""}`;
    li.appendChild(meta);

    const tagsDiv = document.createElement("div");
    tagsDiv.className = "tags";
    for (const t of entry.tags || []) {
      const span = document.createElement("span");
      span.textContent = t;
      tagsDiv.appendChild(span);
    }
    li.appendChild(tagsDiv);

    const servicesDiv = document.createElement("div");
    servicesDiv.className = "services";
    for (const s of entry.services || []) {
      const span = document.createElement("span");
      span.textContent = `${s.name} ${s.protocol}/${s.port}`;
      servicesDiv.appendChild(span);
    }
    li.appendChild(servicesDiv);

    const dl = document.createElement("dl");
    dl.className = "aliases";
    const aliasesBySource = {};
    for (const alias of entry.aliases || []) {
      (aliasesBySource[alias.source] ||= []).push(alias);
    }
    for (const [source, aliases] of Object.entries(aliasesBySource)) {
      const dt = document.createElement("dt");
      dt.textContent = source;
      dl.appendChild(dt);
      const dd = document.createElement("dd");
      dd.textContent = aliases
        .map((a) => `${a.value}${a.ecosystem ? ` (${a.ecosystem})` : ""} — ${a.confidence}`)
        .join(", ");
      dl.appendChild(dd);
    }
    li.appendChild(dl);

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
