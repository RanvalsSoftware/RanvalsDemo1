from . import models
from . import controllers


def post_init_hook(env):
    """Freeze a best-effort company snapshot for pre-existing attendances."""
    env.cr.execute(
        """
        UPDATE hr_attendance AS attendance
           SET ranvals_company_snapshot_id = version.company_id
          FROM hr_employee AS employee
          JOIN hr_version AS version ON version.id = employee.current_version_id
         WHERE attendance.employee_id = employee.id
           AND attendance.ranvals_company_snapshot_id IS NULL
           AND version.company_id IS NOT NULL
        """
    )
