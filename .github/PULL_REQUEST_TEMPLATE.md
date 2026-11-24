## What does this PR add or change?

<!-- e.g. "Adds vendor `acme` and product `acme/widget`" -->

## Checklist

- [ ] `uv run tools/validate.py` passes locally
- [ ] `uv run pytest` passes locally
- [ ] No duplicate `(source, value)` alias pairs introduced
- [ ] Every tag referenced already exists in `data/taxonomy/tags.yaml` (if not, that's a separate PR — see CONTRIBUTING.md)
- [ ] Every alias cites its source (`nvd`, `cisa_kev`, `osv`, `endoflife`; products may also use `banner` and `name`)
- [ ] Every `domain`, `banner` and `name` value is backed by a public source cited in this PR, and none identifies a private host, organization or internal system
- [ ] This PR does not mix a new taxonomy tag with the product/vendor that uses it
- [ ] If this PR needs a new alias source not yet in the schema, that's a separate CODEOWNERS-gated PR to `data/schema/**` first (same rule as new tags)
