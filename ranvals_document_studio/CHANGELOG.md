# DocuCraft Changelog

## 19.0.3.3.0 — 2026-09-27

- Split the export dialog into focused Preview and Fields pages while keeping
  the download actions fixed and easy to reach.
- Fixed the first field toggle dropping hidden technical values, breaking the
  live preview and preventing the transient export form from being saved.
- Removed smart-button counters, warning payloads, widget JSON and standard
  line-form implementation fields from automatic field discovery while
  retaining real list columns and customer-created Studio fields.
- Normalized copied field labels and samples so tabs, line breaks and blank
  widget headings cannot appear as `&#x9;`, `&#x20;` or raw JSON in the chooser.
- Added automatic company-language documents, optional customer/vendor
  language and a manual language override; automatic labels follow the
  resolved, supported active Odoo language without overwriting user-edited
  headings.
- Replaced the small generic template thumbnail with the selected real QWeb
  layout rendered on a safe, localized A4-style sample.
- Expanded heading and body typography from four to twenty-three choices,
  including Odoo-bundled Lato, Roboto, Open Sans, Montserrat, Raleway, Oswald,
  Tajawal and Fira Mono, with matching PDF, live-preview, JSON and DOCX rules.

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
