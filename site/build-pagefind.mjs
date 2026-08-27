// Builds a Pagefind search index from the flattened aliases.json, using
// Pagefind's custom-record API — there are no HTML pages to crawl here, the
// data is structured JSON. Each record's `content` is just enough text to
// match against (name, tags, alias values); `url` points at the small
// per-entry detail file build_index.py already wrote under index/entries/,
// which the site fetches directly for the matched entries' full detail.
import { parseArgs } from "node:util";
import { readFile } from "node:fs/promises";
import * as pagefind from "pagefind";

const { values } = parseArgs({
  options: {
    index: { type: "string" },
    output: { type: "string" },
  },
});

if (!values.index || !values.output) {
  console.error("Usage: node build-pagefind.mjs --index <path to aliases.json> --output <pagefind output dir>");
  process.exit(1);
}

const { entries } = JSON.parse(await readFile(values.index, "utf-8"));

const { index, errors: createErrors } = await pagefind.createIndex({});
if (createErrors.length > 0) {
  console.error("Failed to create Pagefind index:", createErrors);
  process.exit(1);
}

for (const entry of entries) {
  const aliasValues = (entry.aliases || []).map((a) => a.value);
  const content = [entry.name, ...(entry.tags || []), ...aliasValues].join(" ");

  const { errors } = await index.addCustomRecord({
    url: `index/entries/${entry.slug}.json`,
    content,
    language: "en",
    meta: { title: entry.name },
    filters: { canonical_type: [entry.canonical_type] },
  });
  if (errors.length > 0) {
    console.error(`Failed to index ${entry.slug}:`, errors);
    process.exit(1);
  }
}

const { errors: writeErrors, outputPath } = await index.writeFiles({ outputPath: values.output });
if (writeErrors.length > 0) {
  console.error("Failed to write Pagefind index:", writeErrors);
  process.exit(1);
}

console.log(`Indexed ${entries.length} entries to ${outputPath}`);
await pagefind.close();
