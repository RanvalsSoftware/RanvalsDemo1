{
    "name": "Employee QR Attendance",
    "version": "19.0.3.0.0",
    "category": "Human Resources/Attendances",
    "summary": "QR check-in/out, weekly reports, overtime insights, and payroll-preparation data",
    "description": """
Employee QR Attendance adds secure mobile QR check-in and check-out to Odoo
Attendances. It includes personal PIN authentication, trusted-network controls,
remembered-device revocation, multi-company isolation, weekly and monthly
attendance periods, exception analysis, PDF summaries, and payroll-preparation CSV
hour exports. The core app prepares attendance inputs; it does not create
payslips or calculate salary amounts.
""",
    "author": "Ranvals Software",
    "maintainer": "Ranvals Software",
    "license": "LGPL-3",
    "depends": ["hr_attendance", "web"],
    "external_dependencies": {"python": ["qrcode"]},
    "post_init_hook": "post_init_hook",
    "data": [
        "security/ir.model.access.csv",
        "security/rules.xml",
        "views/attendance_views.xml",
        "views/reporting_views.xml",
        "views/mobile_templates.xml",
        "report/reporting_templates.xml",
        "data/cleanup_cron.xml",
    ],
    "assets": {
        "ranvals_hr_attendance_qr.assets_public": [
            "ranvals_hr_attendance_qr/static/src/css/mobile.css",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
