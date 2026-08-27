## What does this PR add or change?

<!-- e.g. "Adds vendor `acme` and product `acme/widget`" -->

## Checklist

- [ ] `uv run tools/validate.py` passes locally
- [ ] `uv run pytest` passes locally
- [ ] No duplicate `(source, value)` alias pairs introduced
- [ ] Every tag referenced already exists in `taxonomy/tags.yaml` (if not, that's a separate PR — see CONTRIBUTING.md)
- [ ] Every alias cites its source (`nvd_cpe`, `cisa_kev`, `osv`, `endoflife`)
- [ ] This PR does not mix a new taxonomy tag with the product/vendor that uses it
- [ ] If this PR needs a new alias source not yet in the schema, that's a separate CODEOWNERS-gated PR to `schema/**` first (same rule as new tags)
