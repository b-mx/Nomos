# Security Policy

Nomos publishes a static mapping of vendor/product identities; it has no
runtime service, database, or credentials, so most classes of
vulnerability (injection, auth bypass, etc.) don't apply to the data
itself.

If you find a security issue in the *tooling* (`tools/validate.py`,
`tools/suggest_match.py`, `tools/build_index.py`), the CI workflows, or a
supply-chain concern (a compromised dependency, a malicious pull request
payload), please report it privately via GitHub's "Report a vulnerability"
button under this repo's Security tab rather than opening a public issue.

We aim to acknowledge reports within 5 business days.
