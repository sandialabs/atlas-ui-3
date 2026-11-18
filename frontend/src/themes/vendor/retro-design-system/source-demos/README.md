Vendored copies of the 53 standalone `styles/*/index.html` demos from
NovusGFX/retro-design-system at commit 8988f50a514e7f845dbe5ad190faa2afd926f0b4
(MIT; see ../LICENSE).

These files are inputs to `frontend/scripts/build-retro-catalog.py` only.
The generator extracts and scopes their CSS; it does not emit scripts,
`@font-face`, `@import`, remote `url()`, global resets, or animations
into the Atlas bundle.
