# Employee QR Attendance

Secure QR employee check-in/out and auditable attendance reporting for Odoo 19.

## Scope

- Standard `hr.employee` and `hr.attendance` integration
- Personal code/PIN authentication without an Odoo user per employee
- HTTPS and trusted public-IP enforcement
- Signed, idempotent and concurrency-safe attendance transitions
- Revocable temporary or remembered phone sessions
- Weekly, monthly and custom attendance-period snapshots
- Planned, worked, scheduled, outside-schedule and shortfall hours
- Lateness, early leave, missing day, missing check-out and anomaly controls
- Odoo-approved overtime and pay-rate breakdown
- Flexible-schedule reconciliation at period level
- Live pivot/graph views, PDF summary, detailed CSV and payroll-preparation CSV

The payroll-preparation export contains attendance metrics and overtime rates
only. `REGULAR`, `UNDERTIME`, and `OVERTIME` are independent input metrics, not a
pay formula. Leave/work-entry codes remain the responsibility of the payroll
system. The module does not create a payslip or calculate salary amounts.
Country-specific Odoo Enterprise Payroll integration should be provided by a
separate bridge add-on.

For Turkish installation and operating guidance, read `README_TR.md`.

## Dependencies

- Odoo 19 `hr_attendance`
- Odoo 19 `web`
- Python package `qrcode` (already present in the official Odoo 19 requirements)

## Reporting workflow

1. Generate a weekly, monthly or custom period.
2. Review exceptions and approve Odoo overtime entries.
3. Mark the snapshot as reviewed.
4. Lock the clean all-channel period.
5. Download the PDF, detailed CSV or payroll-preparation CSV.

QR-only periods are analytics snapshots and cannot be locked for payroll. Open
attendances, pending overtime approvals and anomalous future/long records block
locking.

## License

LGPL-3. See `LICENSE`.
