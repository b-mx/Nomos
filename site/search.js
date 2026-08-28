// Search is powered by Pagefind (chunked WASM index, fetches only the
// shards a query touches) instead of loading the whole dataset up front —
// this is what keeps page weight flat as the vendor/product count grows.
// Each search result only carries a `url`; that's a pointer to the small
// per-entry detail file build_index.py writes under index/entries/<slug>.json,
// which is fetched on demand for the entries actually shown.
//
// Results are grouped by vendor (one card per vendor, its matching products
// nested inside) rather than rendered as a flat list — this mirrors how
// data/vendors/<id>/products/*.yaml are actually organised on disk. A product
// match whose vendor wasn't independently a search hit still needs the
// vendor's name/icon for the card header, so its entry is fetched too.

const REPO_BLOB = "https://github.com/b-mx/Nomos/blob/main/";

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

function sourcePath(entry) {
  return entry.canonical_type === "product"
    ? `data/vendors/${entry.vendor_id}/products/${entry.product_id}.yaml`
    : `data/vendors/${entry.vendor_id}/vendor.yaml`;
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

function appendIcon(container, icon, size) {
  const iconUrl = iconifyUrl(icon);
  if (!iconUrl) return;
  const img = document.createElement("img");
  img.className = "icon";
  img.src = iconUrl;
  img.alt = "";
  img.width = size;
  img.height = size;
  img.loading = "lazy";
  img.addEventListener("error", () => img.remove());
  container.appendChild(img);
}

function buildSourceLink(entry) {
  const path = sourcePath(entry);
  const a = document.createElement("a");
  a.className = "source-link";
  a.href = REPO_BLOB + path;
  a.title = path;
  const img = document.createElement("img");
  img.src = "https://api.iconify.design/octicon/file-code-16.svg";
  img.alt = "";
  img.width = 14;
  img.height = 14;
  img.loading = "lazy";
  img.addEventListener("error", () => {
    img.remove();
    a.textContent = ".yaml";
  });
  a.appendChild(img);
  return a;
}

function buildAliasList(aliases) {
  const dl = document.createElement("dl");
  dl.className = "aliases";
  const aliasesBySource = {};
  for (const alias of aliases || []) {
    (aliasesBySource[alias.source] ||= []).push(alias);
  }
  for (const [source, list] of Object.entries(aliasesBySource)) {
    const dt = document.createElement("dt");
    dt.textContent = source;
    dt.className = `source-${source}`;
    dl.appendChild(dt);
    const dd = document.createElement("dd");
    dd.textContent = list
      .map((a) => `${a.value}${a.ecosystem ? ` (${a.ecosystem})` : ""} — ${a.confidence}`)
      .join(", ");
    dl.appendChild(dd);
  }
  return dl;
}

function buildProductRow(product, first, vendorIconFallback) {
  const row = document.createElement("div");
  row.className = first ? "product-row product-row-first" : "product-row";

  const header = document.createElement("div");
  header.className = "product-row-header";
  appendIcon(header, product.icon || vendorIconFallback, 20);
  const h3 = document.createElement("h3");
  h3.textContent = product.name;
  header.appendChild(h3);
  const idCode = document.createElement("code");
  idCode.className = "product-id";
  idCode.textContent = product.product_id;
  header.appendChild(idCode);
  header.appendChild(buildSourceLink(product));
  row.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `product · ${product.type}`;
  row.appendChild(meta);

  const tagsDiv = document.createElement("div");
  tagsDiv.className = "tags";
  for (const t of product.tags || []) {
    const span = document.createElement("span");
    span.textContent = t;
    tagsDiv.appendChild(span);
  }
  row.appendChild(tagsDiv);

  const servicesDiv = document.createElement("div");
  servicesDiv.className = "services";
  for (const s of product.services || []) {
    const span = document.createElement("span");
    span.textContent = `${s.name} ${s.protocol}/${s.port}`;
    servicesDiv.appendChild(span);
  }
  row.appendChild(servicesDiv);

  row.appendChild(buildAliasList(product.aliases));
  return row;
}

function buildVendorCard(vendor, products) {
  const li = document.createElement("li");
  li.className = "vendor-card";

  const left = document.createElement("div");
  left.className = "vendor-card-left";

  const header = document.createElement("div");
  header.className = "vendor-card-header";
  appendIcon(header, vendor.icon, 24);
  const h2 = document.createElement("h2");
  h2.textContent = vendor.name;
  header.appendChild(h2);
  header.appendChild(buildSourceLink(vendor));
  left.appendChild(header);

  const meta = document.createElement("div");
  meta.className = "meta";
  const idCode = document.createElement("code");
  idCode.textContent = vendor.vendor_id;
  meta.append("vendor · ", idCode);
  left.appendChild(meta);

  left.appendChild(buildAliasList(vendor.aliases));

  const count = document.createElement("div");
  count.className = "vendor-card-count";
  count.textContent = `${products.length} product${products.length === 1 ? "" : "s"} shown`;
  left.appendChild(count);

  const right = document.createElement("div");
  right.className = "vendor-card-right";
  if (products.length === 0) {
    const none = document.createElement("div");
    none.className = "meta";
    none.textContent = "No matching products.";
    right.appendChild(none);
  } else {
    products.forEach((p, i) => right.appendChild(buildProductRow(p, i === 0, vendor.icon)));
  }

  li.appendChild(left);
  li.appendChild(right);
  return li;
}

function renderResults(groups) {
  const list = document.getElementById("results");
  list.innerHTML = "";
  for (const { vendor, products } of groups) {
    list.appendChild(buildVendorCard(vendor, products));
  }
}

async function groupByVendor(entries) {
  const groups = new Map();
  const vendorFetches = new Map();

  for (const entry of entries) {
    if (entry.canonical_type !== "vendor") continue;
    const existing = groups.get(entry.vendor_id);
    groups.set(entry.vendor_id, { vendor: entry, products: existing ? existing.products : [] });
  }
  for (const entry of entries) {
    if (entry.canonical_type !== "product") continue;
    let group = groups.get(entry.vendor_id);
    if (!group) {
      if (!vendorFetches.has(entry.vendor_id)) {
        vendorFetches.set(entry.vendor_id, fetchEntry(`index/entries/${entry.vendor_id}.json`));
      }
      group = { vendor: null, products: [] };
      groups.set(entry.vendor_id, group);
    }
    group.products.push(entry);
  }

  for (const [vendorId, vendorPromise] of vendorFetches) {
    const vendor = await vendorPromise;
    const group = groups.get(vendorId);
    if (group && !group.vendor) group.vendor = vendor;
  }

  return [...groups.values()].filter((g) => g.vendor);
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

  const groups = await groupByVendor(entries);
  if (requestId !== latestRequestId) return;

  const total = search.results.length;
  statusEl.textContent = `${total} match${total === 1 ? "" : "es"} for "${query}"${
    total > entries.length ? ` (showing ${entries.length})` : ""
  }.`;
  renderResults(groups);
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
