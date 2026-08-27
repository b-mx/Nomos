// Search is powered by Pagefind (chunked WASM index, fetches only the
// shards a query touches) instead of loading the whole dataset up front —
// this is what keeps page weight flat as the vendor/product count grows.
// Each search result only carries a `url`; that's a pointer to the small
// per-entry detail file build_index.py writes under index/entries/<slug>.json,
// which is fetched on demand for the entries actually shown.

let pagefind = null;
let latestRequestId = 0;

function iconifyUrl(icon) {
  if (!icon) return null;
  const sep = icon.indexOf(":");
  if (sep === -1) return null;
  const collection = icon.slice(0, sep);
  const name = icon.slice(sep + 1);
  return `https://api.iconify.design/${collection}/${name}.svg`;
}

async function fetchEntry(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

function renderResults(entries) {
  const list = document.getElementById("results");
  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    li.className = "result";

    const title =
      entry.canonical_type === "product"
        ? `${entry.name} (${entry.vendor_id}/${entry.product_id})`
        : `${entry.name} (${entry.vendor_id})`;

    const header = document.createElement("div");
    header.className = "result-header";

    const iconUrl = iconifyUrl(entry.icon);
    if (iconUrl) {
      const img = document.createElement("img");
      img.className = "icon";
      img.src = iconUrl;
      img.alt = "";
      img.width = 24;
      img.height = 24;
      img.loading = "lazy";
      img.addEventListener("error", () => img.remove());
      header.appendChild(img);
    }

    const h2 = document.createElement("h2");
    h2.textContent = title;
    header.appendChild(h2);

    li.appendChild(header);

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

async function runSearch(query) {
  const requestId = ++latestRequestId;
  const statusEl = document.getElementById("status");

  if (!query) {
    renderResults([]);
    statusEl.textContent = "Type to search vendors and products.";
    return;
  }

  statusEl.textContent = "Searching…";
  const search = await pagefind.search(query);
  if (requestId !== latestRequestId) return;

  const top = search.results.slice(0, 30);
  const fragments = await Promise.all(top.map((r) => r.data()));
  if (requestId !== latestRequestId) return;

  const entries = (await Promise.all(fragments.map((f) => fetchEntry(f.url)))).filter(Boolean);
  if (requestId !== latestRequestId) return;

  const total = search.results.length;
  statusEl.textContent = `${total} match${total === 1 ? "" : "es"} for "${query}"${
    total > entries.length ? ` (showing ${entries.length})` : ""
  }.`;
  renderResults(entries);
}

async function main() {
  const statusEl = document.getElementById("status");
  statusEl.textContent = "Loading search index…";

  try {
    pagefind = await import("./pagefind/pagefind.js");
    await pagefind.init();
  } catch (err) {
    statusEl.textContent = `Failed to load search index: ${err.message}`;
    return;
  }

  statusEl.textContent = "Type to search vendors and products.";

  const searchBox = document.getElementById("search-box");
  let debounceTimer = null;
  searchBox.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const query = searchBox.value.trim();
    debounceTimer = setTimeout(() => runSearch(query), 150);
  });
}

main();
