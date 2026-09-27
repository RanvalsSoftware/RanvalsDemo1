"""Backfill the company snapshot introduced for stable historical reports."""


def migrate(cr, version):
    cr.execute(
        """
        UPDATE hr_attendance AS attendance
           SET ranvals_company_snapshot_id = employee_version.company_id
          FROM hr_employee AS employee
          JOIN hr_version AS employee_version
            ON employee_version.id = employee.current_version_id
         WHERE attendance.employee_id = employee.id
           AND attendance.ranvals_company_snapshot_id IS NULL
           AND employee_version.company_id IS NOT NULL
        """
    )
