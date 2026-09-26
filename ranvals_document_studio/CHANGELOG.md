# DocuCraft Changelog

## 19.0.3.2.0 — 2026-09-27

- Replaced the generic placeholder thumbnail with a live preview of the
  selected document, language and one of the fourteen real QWeb layouts.
- Added an access-aware field chooser above the preview. Fields visible on the
  document and line screens, including Studio fields, appear automatically.
- Added per-export green/on and grey/off controls, editable document labels
  and drag ordering for document metadata and line columns.
- Applied the same immutable selection to PDF, editable Word, visual Word,
  PNG, ZIP and background jobs.
- Fixed the all-fields-off fallback so disabled metadata/columns cannot return
  silently, and made larger metadata sets wrap into four-column rows.
- Kept conditional fields stable across mixed-record batches, preserved
  hide-if-empty rules and hardened transient field ownership.
- Kept custom field selections identical when an export starts from a native
  or Studio source report.
- Reworked the print dialog cards, scrolling and mobile footer layout.

## 19.0.3.1.0 — 2026-09-27

- Fixed the Odoo 19 Users/Access Rights Owl error caused by an XML
  metacharacter in the translated DocuCraft category name.
- Replaced the separate PDF, Word and More buttons with one branded
  **DocuCraft Yazdır** flow on sales, invoices and purchases.
- Kept one DocuCraft entry per model in the Print menu and retired the legacy
  quick-format/report bindings without deleting their compatible XML IDs.
- Removed file downloads from the Studio design-settings panel; document
  output now starts from the relevant business document.
- Renamed the six sector-specific templates and added eight new corporate
  layouts, for a total of fourteen designs.
- Improved downloaded filenames to use polished template names while keeping
  stable technical template codes.
