# Odoo Apps Publishing Checklist

The module is **not published** by this delivery.

Before registering the repository on Odoo Apps:

- Provide and verify the real `website` and `support` email manifest values.
- Decide whether the release remains LGPL-3 or uses another legally appropriate
  license. Do not change the current license without a code-ownership review.
- Capture real English-language screenshots from a staging Odoo 19 database:
  mobile authentication, successful check-in/out, station settings, weekly
  period, exceptions, live pivot and payroll export.
- Add a real product screenshot as `images/main_screenshot.png` and reference it
  from the manifest. Never use a generated or misleading interface screenshot.
- Review every English store claim against the final tested build.
- Add English source UI plus `i18n/tr.po` before targeting an international
  audience; the current operational interface is primarily Turkish.
- Run clean install, upgrade, uninstall, multi-company, HTTP, PDF, CSV,
  concurrency and mobile acceptance tests on the release commit.
- Remove generated caches and development-only files from the ZIP/repository.
- Publish from the repository's `19.0` branch only after the support process,
  price/currency (if any), screenshots and license are final.
