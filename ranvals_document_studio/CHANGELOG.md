# DocuCraft Changelog

## 19.0.3.5.0 — 2026-09-27

- Fixed the template form shell so the designer remains beside the DocuCraft
  sidebar instead of being pushed below the visible viewport.
- Fixed designer logos under Odoo's binary-size RPC context and scoped the
  static-preview image limit so document logos retain their configured size.
- Added regression coverage for binary-size logo previews and completed the
  real-product Odoo Apps gallery, Word output proof and searchable product name.
- Documented the PDF-engine requirement separately from native editable Word.

## 19.0.3.4.0 — 2026-09-27

- Promoted editable Word to a first-class export with localized contact labels,
  verified native tables and cleaner, language-neutral download names.
- Standardized the module's user-interface source language on English and
  completed installable catalogs for English (US/UK), Turkish, German,
  French, Spanish, Italian, Portuguese, Russian and Arabic.
- Made company-language document selection consistent across PDF, editable
  Word, design-preserved Word, PNG, ZIP, live preview and queued exports.
- Made archived templates reliably searchable with explicit Active, Archived
  and All filters while retaining Odoo's normal active-only opening view.
- Prepared the Odoo Apps edition with OPL-1 metadata, EUR 87 pricing, a new
  store icon, detailed product page and real English product screenshots.

## 19.0.3.3.1 — 2026-09-27

- Added Active, Archived and All filters so archived DocuCraft templates can
  always be found without changing the normal active-only opening view.
- Added a clear archived ribbon and kept the status toggle available in both
  list and form views for one-click reactivation.
- Removed the redundant default Active search chip and separated status from
  default-template filtering for predictable search combinations.

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
