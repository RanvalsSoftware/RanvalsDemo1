"""High-value registry tests for attendance reporting and payroll preparation."""

from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "ranvals_qr")
class TestRanvalsQRAttendanceReporting(TransactionCase):
    REPORT_NOW = datetime(2026, 3, 9, 12, 0, 0)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write(
            {
                "ranvals_qr_late_grace_minutes": 5,
                "ranvals_qr_early_grace_minutes": 5,
                "ranvals_qr_shift_merge_minutes": 90,
            }
        )
        cls.calendar = cls.env["resource.calendar"].create(
            {
                "name": "QR Reporting Test — UTC 09:00–17:00",
                "company_id": cls.company.id,
                "tz": "UTC",
                "attendance_ids": [Command.clear()]
                + [
                    Command.create(
                        {
                            "name": f"Weekday {dayofweek}",
                            "dayofweek": dayofweek,
                            "hour_from": 9.0,
                            "hour_to": 17.0,
                            "day_period": "full_day",
                        }
                    )
                    for dayofweek in ("0", "1", "2", "3", "4")
                ],
            }
        )
        cls.department = cls.env["hr.department"].create(
            {"name": "+Finance", "company_id": cls.company.id}
        )
        common_employee_values = {
            "company_id": cls.company.id,
            "resource_calendar_id": cls.calendar.id,
            "tz": "UTC",
            "date_version": date(2026, 1, 1),
            "contract_date_start": date(2026, 1, 1),
        }
        cls.employee = cls.env["hr.employee"].create(
            {
                **common_employee_values,
                "name": "=Payroll Test",
                "department_id": cls.department.id,
            }
        )
        cls.audit_employee = cls.env["hr.employee"].create(
            {**common_employee_values, "name": "QR Audit Employee"}
        )
        cls.manager = new_test_user(
            cls.env,
            login="ranvals_qr_reporting_manager",
            groups="hr_attendance.group_hr_attendance_manager,hr.group_hr_user",
            company_id=cls.company.id,
            lang="en_US",
        )
        cls.public_manager = new_test_user(
            cls.env,
            login="ranvals_qr_public_reporting_manager",
            groups="base.group_user,hr_attendance.group_hr_attendance_manager",
            company_id=cls.company.id,
            lang="en_US",
        )
        cls.station = cls.env["ranvals.qr.station"].create(
            {
                "name": "Reporting Test Station",
                "brand_name": "Reporting Test",
                "company_id": cls.company.id,
                "base_url": "https://reports.example.test",
                "allowed_networks": "8.8.8.8/32",
            }
        )

    def _create_batch(
        self,
        date_from,
        date_to,
        *,
        employees=None,
        attendance_scope="all",
        revision=1,
        period_type="custom",
        user=None,
    ):
        employees = employees or self.employee
        user = user or self.manager
        batch_model = self.env["ranvals.qr.report.batch"].with_user(user)
        return batch_model.create(
            {
                "name": batch_model._period_name(
                    date_from, date_to, revision, attendance_scope
                ),
                "company_id": self.company.id,
                "date_from": date_from,
                "date_to": date_to,
                "period_type": period_type,
                "attendance_scope": attendance_scope,
                "revision": revision,
                "employee_ids": [Command.set(employees.ids)],
                "late_grace_minutes": 5,
                "early_grace_minutes": 5,
                "shift_merge_minutes": 90,
            }
        )

    def _calculate(self, batch, *, user=None):
        user = user or self.manager
        with patch.object(fields.Datetime, "now", return_value=self.REPORT_NOW):
            batch.with_user(user).action_calculate()
        return batch

    def _clear_employee_overtime(self, employee):
        self.env["hr.attendance.overtime.line"].sudo().search(
            [("employee_id", "=", employee.id)]
        ).unlink()

    def test_zero_rate_is_preserved_and_negative_approved_is_not_overtime(self):
        work_date = date(2026, 3, 2)
        attendance = self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.audit_employee.id,
                "check_in": datetime(2026, 3, 2, 9, 0),
                "check_out": datetime(2026, 3, 2, 17, 0),
            }
        )
        self._clear_employee_overtime(self.audit_employee)
        overtime_lines = self.env["hr.attendance.overtime.line"].sudo().create(
            [
                {
                    "employee_id": self.audit_employee.id,
                    "date": work_date,
                    "duration": 2.0,
                    "manual_duration": 2.0,
                    "time_start": attendance.check_in,
                    "time_stop": attendance.check_out + timedelta(hours=2),
                    "amount_rate": 0.0,
                },
                {
                    "employee_id": self.audit_employee.id,
                    "date": work_date,
                    "duration": -1.0,
                    "manual_duration": -1.0,
                    "time_start": attendance.check_in,
                    "time_stop": attendance.check_in + timedelta(hours=1),
                    "amount_rate": 1.5,
                },
            ]
        )
        overtime_lines[0].write(
            {"status": "approved", "manual_duration": 2.0, "amount_rate": 0.0}
        )
        overtime_lines[1].write(
            {"status": "approved", "manual_duration": -1.0, "amount_rate": 1.5}
        )

        batch = self._calculate(
            self._create_batch(work_date, work_date, employees=self.audit_employee)
        )
        exported_overtime = batch.payroll_line_ids.filtered(
            lambda line: line.code == "OVERTIME"
        )

        self.assertEqual(len(exported_overtime), 1)
        self.assertAlmostEqual(exported_overtime.hours, 2.0, places=4)
        self.assertAlmostEqual(exported_overtime.rate, 0.0, places=4)
        self.assertAlmostEqual(exported_overtime.weighted_hours, 0.0, places=4)
        self.assertAlmostEqual(batch.approved_overtime_hours, 2.0, places=4)
        self.assertFalse(
            batch.payroll_line_ids.filtered(
                lambda line: line.code == "OVERTIME" and line.hours < 0
            )
        )

    def test_flexible_week_redistributes_hours_without_daily_penalties(self):
        monday = date(2026, 3, 2)
        sunday = date(2026, 3, 8)
        flexible_calendar = self.env["resource.calendar"].create(
            {
                "name": "QR Reporting Test — Flexible 40 Hours",
                "company_id": self.company.id,
                "tz": "UTC",
                "hours_per_day": 8.0,
                "hours_per_week": 40.0,
                "full_time_required_hours": 40.0,
                "flexible_hours": True,
            }
        )
        flexible_employee = self.env["hr.employee"].create(
            {
                "name": "Flexible Weekly Employee",
                "company_id": self.company.id,
                "resource_calendar_id": flexible_calendar.id,
                "tz": "UTC",
                "date_version": date(2026, 1, 1),
                "contract_date_start": date(2026, 1, 1),
            }
        )
        daily_hours = (10, 6, 8, 8, 8)
        self.env["hr.attendance"].sudo().create(
            [
                {
                    "employee_id": flexible_employee.id,
                    "check_in": datetime(2026, 3, 2 + offset, 8, 0),
                    "check_out": datetime(2026, 3, 2 + offset, 8, 0)
                    + timedelta(hours=hours),
                }
                for offset, hours in enumerate(daily_hours)
            ]
        )
        self._clear_employee_overtime(flexible_employee)

        batch = self._calculate(
            self._create_batch(
                monday,
                sunday,
                employees=flexible_employee,
                period_type="week",
            )
        )

        self.assertAlmostEqual(batch.planned_hours, 40.0, places=4)
        self.assertAlmostEqual(batch.worked_hours, 40.0, places=4)
        self.assertAlmostEqual(batch.scheduled_worked_hours, 40.0, places=4)
        self.assertAlmostEqual(batch.shortfall_hours, 0.0, places=4)
        self.assertAlmostEqual(batch.outside_schedule_hours, 0.0, places=4)
        self.assertEqual(set(batch.day_line_ids.mapped("is_flexible")), {True})
        self.assertFalse(batch.day_line_ids.filtered(lambda line: line.late_minutes))
        self.assertFalse(batch.day_line_ids.filtered(lambda line: line.early_minutes))
        payroll = {
            line.code: line.hours for line in batch.payroll_line_ids
        }
        self.assertEqual(set(payroll), {"REGULAR"})
        self.assertAlmostEqual(payroll["REGULAR"], 40.0, places=4)

    def test_open_attendance_started_before_period_still_blocks(self):
        work_date = date(2026, 3, 2)
        check_in = datetime(2026, 2, 27, 9, 0)
        self.env["hr.attendance"].sudo().create(
            {"employee_id": self.audit_employee.id, "check_in": check_in}
        )
        self._clear_employee_overtime(self.audit_employee)

        batch = self._calculate(
            self._create_batch(work_date, work_date, employees=self.audit_employee)
        )

        self.assertEqual(len(batch.day_line_ids), 1)
        self.assertEqual(batch.day_line_ids.work_date, work_date)
        self.assertEqual(batch.day_line_ids.first_check_in, check_in)
        self.assertEqual(batch.day_line_ids.status, "incomplete")
        self.assertEqual(batch.open_attendance_count, 1)
        self.assertEqual(batch.blocker_count, 1)
        self.assertFalse(batch.payroll_ready)

    def test_stale_source_is_refused_when_locking(self):
        work_date = date(2026, 3, 2)
        attendance = self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.audit_employee.id,
                "check_in": datetime(2026, 3, 2, 9, 0),
                "check_out": datetime(2026, 3, 2, 17, 0),
            }
        )
        self._clear_employee_overtime(self.audit_employee)
        batch = self._calculate(
            self._create_batch(work_date, work_date, employees=self.audit_employee)
        )
        original_hash = batch.source_hash
        batch.with_user(self.manager).action_mark_reviewed()

        attendance.write({"check_out": datetime(2026, 3, 2, 17, 30)})
        self._clear_employee_overtime(self.audit_employee)
        with self.assertRaises(UserError):
            batch.with_user(self.manager).action_lock()

        self.assertEqual(batch.state, "review")
        self.assertFalse(batch.locked_at)
        self.assertEqual(batch.source_hash, original_hash)

    def test_direct_audit_field_and_state_forgery_is_rejected(self):
        work_date = date(2026, 3, 2)
        batch_model = self.env["ranvals.qr.report.batch"].with_user(self.manager)
        forged_values = {
            "name": "Forged locked report",
            "company_id": self.company.id,
            "date_from": work_date,
            "date_to": work_date,
            "period_type": "custom",
            "attendance_scope": "all",
            "revision": 1,
            "employee_ids": [Command.set(self.employee.ids)],
            "late_grace_minutes": 5,
            "early_grace_minutes": 5,
            "shift_merge_minutes": 90,
            "state": "locked",
            "source_hash": "forged",
        }
        with mute_logger("odoo.tools.translate"), self.assertRaises(AccessError):
            batch_model.create(forged_values)

        batch = self._create_batch(work_date, work_date)
        with mute_logger("odoo.tools.translate"), self.assertRaises(AccessError):
            batch.with_user(self.manager).write(
                {
                    "state": "locked",
                    "generated_at": self.REPORT_NOW,
                    "locked_by_id": self.manager.id,
                    "source_hash": "forged",
                }
            )
        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.generated_at)
        self.assertFalse(batch.source_hash)

    def test_attendance_manager_without_hr_user_can_calculate_public_employees(self):
        work_date = date(2026, 3, 3)
        self.assertFalse(self.public_manager.has_group("hr.group_hr_user"))
        private_employee = self.env["hr.employee"].with_user(
            self.public_manager
        ).browse(self.audit_employee.id)
        with self.assertRaises(AccessError):
            private_employee.check_access("read")
        self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.audit_employee.id,
                "check_in": datetime(2026, 3, 3, 9, 0),
                "check_out": datetime(2026, 3, 3, 17, 0),
            }
        )
        self._clear_employee_overtime(self.audit_employee)
        public_employee = self.env["hr.employee.public"].browse(
            self.audit_employee.id
        )

        batch = self._calculate(
            self._create_batch(
                work_date,
                work_date,
                employees=public_employee,
                user=self.public_manager,
            ),
            user=self.public_manager,
        )

        self.assertEqual(batch.employee_ids._name, "hr.employee.public")
        self.assertEqual(batch.generated_by_id, self.public_manager)
        self.assertAlmostEqual(batch.worked_hours, 8.0, places=4)
        self.assertTrue(batch.payroll_ready)

    def test_cross_company_and_qr_attendance_reassignment_is_rejected(self):
        other_company = self.env["res.company"].sudo().create(
            {"name": "QR Reporting Other Company"}
        )
        other_employee = self.env["hr.employee"].sudo().with_company(
            other_company
        ).create(
            {
                "name": "Other Company Employee",
                "company_id": other_company.id,
                "resource_calendar_id": other_company.resource_calendar_id.id,
                "tz": "UTC",
                "date_version": date(2026, 1, 1),
                "contract_date_start": date(2026, 1, 1),
            }
        )
        standard_attendance = self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 3, 11, 9, 0),
                "check_out": datetime(2026, 3, 11, 17, 0),
            }
        )
        with self.assertRaises(ValidationError):
            standard_attendance.write({"employee_id": other_employee.id})
        self.assertEqual(standard_attendance.employee_id, self.employee)
        self.assertEqual(
            standard_attendance.ranvals_company_snapshot_id, self.company
        )

        qr_attendance = self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 3, 12, 9, 0),
                "check_out": datetime(2026, 3, 12, 17, 0),
                "ranvals_qr_in_station_id": self.station.id,
                "ranvals_qr_out_station_id": self.station.id,
            }
        )
        with self.assertRaises(ValidationError):
            qr_attendance.write({"employee_id": self.audit_employee.id})
        self.assertEqual(qr_attendance.employee_id, self.employee)
        self.assertEqual(qr_attendance.ranvals_company_snapshot_id, self.company)

    def test_pdf_provider_rejects_raw_batch_and_template_renders_after_calculation(self):
        work_date = date(2026, 3, 2)
        batch = self._create_batch(work_date, work_date)
        provider = self.env[
            "report.ranvals_hr_attendance_qr.report_attendance_batch"
        ].with_user(self.manager)
        with self.assertRaises(UserError):
            provider._get_report_values([batch.id])
        with self.assertRaises(UserError):
            batch.with_user(self.manager).action_print_pdf()

        self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 3, 2, 9, 0),
                "check_out": datetime(2026, 3, 2, 17, 0),
            }
        )
        self._clear_employee_overtime(self.employee)
        self._calculate(batch)

        values = provider._get_report_values([batch.id])
        self.assertEqual(values["doc_ids"], [batch.id])
        self.assertEqual(values["docs"], batch)
        self.assertEqual(
            values["summaries"][batch.id][0]["employee_name"], self.employee.name
        )
        action = batch.with_user(self.manager).action_print_pdf()
        self.assertEqual(action["type"], "ir.actions.report")

        report_action = self.env.ref(
            "ranvals_hr_attendance_qr.action_report_attendance_batch"
        )
        html, output_type = self.env["ir.actions.report"].with_user(
            self.manager
        )._render_qweb_html(report_action.id, [batch.id])
        self.assertEqual(output_type, "html")
        self.assertIn(b"Payroll Test", bytes(html))

    def test_weekly_totals_payroll_codes_workflow_and_csv_safety(self):
        monday = date(2026, 3, 2)
        tuesday = date(2026, 3, 3)
        attendances = self.env["hr.attendance"].sudo().create(
            [
                {
                    "employee_id": self.employee.id,
                    "check_in": datetime(2026, 3, 2, 9, 15),
                    "check_out": datetime(2026, 3, 2, 16, 45),
                },
                {
                    "employee_id": self.employee.id,
                    "check_in": datetime(2026, 3, 3, 9, 0),
                    "check_out": datetime(2026, 3, 3, 18, 0),
                },
            ]
        )
        self._clear_employee_overtime(self.employee)
        overtime = self.env["hr.attendance.overtime.line"].sudo().create(
            {
                "employee_id": self.employee.id,
                "date": tuesday,
                "status": "approved",
                "duration": 1.0,
                "manual_duration": 1.0,
                "time_start": attendances[1].check_in,
                "time_stop": attendances[1].check_out,
                "amount_rate": 1.5,
            }
        )
        overtime.write(
            {"status": "approved", "manual_duration": 1.0, "amount_rate": 1.5}
        )

        batch = self._calculate(
            self._create_batch(monday, tuesday, period_type="week")
        )
        lines = {line.work_date: line for line in batch.day_line_ids}
        self.assertEqual(set(lines), {monday, tuesday})

        monday_line = lines[monday]
        self.assertAlmostEqual(monday_line.planned_hours, 8.0, places=4)
        self.assertAlmostEqual(monday_line.worked_hours, 7.5, places=4)
        self.assertAlmostEqual(monday_line.scheduled_worked_hours, 7.5, places=4)
        self.assertAlmostEqual(monday_line.shortfall_hours, 0.5, places=4)
        self.assertAlmostEqual(monday_line.late_minutes_raw, 15.0, places=2)
        self.assertAlmostEqual(monday_line.late_minutes, 10.0, places=2)
        self.assertAlmostEqual(monday_line.early_minutes_raw, 15.0, places=2)
        self.assertAlmostEqual(monday_line.early_minutes, 10.0, places=2)
        self.assertEqual(monday_line.status, "shortfall")

        tuesday_line = lines[tuesday]
        self.assertAlmostEqual(tuesday_line.planned_hours, 8.0, places=4)
        self.assertAlmostEqual(tuesday_line.worked_hours, 9.0, places=4)
        self.assertAlmostEqual(tuesday_line.scheduled_worked_hours, 8.0, places=4)
        self.assertAlmostEqual(tuesday_line.outside_schedule_hours, 1.0, places=4)
        self.assertAlmostEqual(tuesday_line.overtime_approved_hours, 1.0, places=4)
        self.assertEqual(tuesday_line.status, "overtime")

        self.assertAlmostEqual(batch.planned_hours, 16.0, places=4)
        self.assertAlmostEqual(batch.worked_hours, 16.5, places=4)
        self.assertAlmostEqual(batch.scheduled_worked_hours, 15.5, places=4)
        self.assertAlmostEqual(batch.outside_schedule_hours, 1.0, places=4)
        self.assertAlmostEqual(batch.shortfall_hours, 0.5, places=4)
        self.assertAlmostEqual(batch.approved_overtime_hours, 1.0, places=4)
        self.assertEqual(batch.blocker_count, 0)
        self.assertTrue(batch.payroll_ready)

        payroll = {
            (line.code, line.rate): (line.hours, line.weighted_hours)
            for line in batch.payroll_line_ids
        }
        self.assertAlmostEqual(payroll[("REGULAR", 1.0)][0], 15.5, places=4)
        self.assertAlmostEqual(payroll[("UNDERTIME", 1.0)][0], 0.5, places=4)
        self.assertAlmostEqual(payroll[("OVERTIME", 1.5)][0], 1.0, places=4)
        self.assertAlmostEqual(payroll[("OVERTIME", 1.5)][1], 1.5, places=4)

        detail_csv = batch.with_user(self.manager)._csv_content("detail").decode(
            "utf-8-sig"
        )
        self.assertIn("Employee ID;Employee;Department", detail_csv)
        self.assertIn("'=Payroll Test", detail_csv)
        self.assertIn("'+Finance", detail_csv)
        with self.assertRaises(UserError):
            batch.with_user(self.manager)._csv_content("payroll")

        batch.with_user(self.manager).action_mark_reviewed()
        batch.with_user(self.manager).action_lock()
        self.assertEqual(batch.state, "locked")
        payroll_csv = batch.with_user(self.manager)._csv_content("payroll").decode(
            "utf-8-sig"
        )
        self.assertIn("Payroll Code;Hours;Rate;Weighted Hours;Source Hash", payroll_csv)
        self.assertIn("OVERTIME;1.0000;1.5000;1.5000", payroll_csv)
        self.assertIn(batch.source_hash, payroll_csv)
        with self.assertRaises(UserError):
            batch.with_user(self.manager).write({"state": "draft"})

    def test_locked_snapshot_is_immutable_and_new_revision_reflects_source_change(self):
        work_date = date(2026, 3, 2)
        attendance = self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.employee.id,
                "check_in": datetime(2026, 3, 2, 9, 0),
                "check_out": datetime(2026, 3, 2, 17, 0),
            }
        )
        self._clear_employee_overtime(self.employee)
        first = self._calculate(self._create_batch(work_date, work_date))
        first.with_user(self.manager).action_mark_reviewed()
        first.with_user(self.manager).action_lock()
        first_hash = first.source_hash
        first_snapshot_name = first.day_line_ids.employee_name

        attendance.check_out = datetime(2026, 3, 2, 17, 30)
        self._clear_employee_overtime(self.employee)
        self.employee.name = "=Payroll Test Updated"
        action = first.with_user(self.manager).action_new_revision()
        second = self.env["ranvals.qr.report.batch"].browse(action["res_id"])

        self.assertEqual(second.revision, 2)
        self.assertEqual(second.state, "draft")
        self.assertNotEqual(second.source_hash, first_hash)
        self.assertEqual(first.day_line_ids.employee_name, first_snapshot_name)
        self.assertEqual(second.day_line_ids.employee_name, "=Payroll Test Updated")
        self.assertAlmostEqual(first.worked_hours, 8.0, places=4)
        self.assertAlmostEqual(second.worked_hours, 8.5, places=4)

    def test_open_attendance_blocks_locking(self):
        work_date = date(2026, 3, 4)
        self.env["hr.attendance"].sudo().create(
            {
                "employee_id": self.audit_employee.id,
                "check_in": datetime(2026, 3, 4, 9, 0),
            }
        )
        self._clear_employee_overtime(self.audit_employee)
        batch = self._calculate(
            self._create_batch(work_date, work_date, employees=self.audit_employee)
        )

        self.assertEqual(len(batch.day_line_ids), 1)
        self.assertEqual(batch.day_line_ids.status, "incomplete")
        self.assertEqual(batch.open_attendance_count, 1)
        self.assertEqual(batch.blocker_count, 1)
        self.assertFalse(batch.payroll_ready)
        batch.with_user(self.manager).action_mark_reviewed()
        with self.assertRaises(UserError):
            batch.with_user(self.manager).action_lock()

    def test_qr_scope_filters_standard_attendance_and_is_never_payroll_ready(self):
        thursday = date(2026, 3, 5)
        friday = date(2026, 3, 6)
        self.env["hr.attendance"].sudo().create(
            [
                {
                    "employee_id": self.audit_employee.id,
                    "check_in": datetime(2026, 3, 5, 9, 0),
                    "check_out": datetime(2026, 3, 5, 17, 0),
                },
                {
                    "employee_id": self.audit_employee.id,
                    "check_in": datetime(2026, 3, 6, 9, 0),
                    "check_out": datetime(2026, 3, 6, 17, 0),
                    "ranvals_qr_in_station_id": self.station.id,
                    "ranvals_qr_out_station_id": self.station.id,
                },
            ]
        )
        self._clear_employee_overtime(self.audit_employee)
        batch = self._calculate(
            self._create_batch(
                thursday,
                friday,
                employees=self.audit_employee,
                attendance_scope="qr",
            )
        )
        lines = {line.work_date: line for line in batch.day_line_ids}

        self.assertEqual(lines[thursday].attendance_count, 0)
        self.assertEqual(lines[thursday].status, "missing")
        self.assertEqual(lines[friday].attendance_count, 1)
        self.assertEqual(lines[friday].qr_attendance_count, 1)
        self.assertEqual(lines[friday].status, "ok")
        self.assertEqual(batch.blocker_count, 1)
        self.assertFalse(batch.payroll_ready)
        with self.assertRaises(UserError):
            batch.with_user(self.manager)._csv_content("payroll")

    def test_fixed_period_boundaries_limits_and_manager_gate(self):
        wizard_model = self.env["ranvals.qr.report.wizard"]
        self.assertEqual(
            wizard_model._period_dates("week", date(2026, 3, 8)),
            {"date_from": date(2026, 3, 2), "date_to": date(2026, 3, 8)},
        )
        self.assertEqual(
            wizard_model._period_dates("month", date(2028, 2, 10)),
            {"date_from": date(2028, 2, 1), "date_to": date(2028, 2, 29)},
        )

        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._create_batch(date(2026, 3, 2), date(2026, 3, 1))
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._create_batch(date(2025, 1, 1), date(2026, 1, 2))

        officer = new_test_user(
            self.env,
            login="ranvals_qr_reporting_officer",
            groups="hr_attendance.group_hr_attendance_officer",
            company_id=self.company.id,
            lang="en_US",
        )
        with mute_logger("odoo.tools.translate"), self.assertRaises(AccessError):
            self.env["ranvals.qr.report.batch"].with_user(officer).with_context(
                lang="en_US"
            ).create(
                {
                    "name": "Unauthorized report",
                    "company_id": self.company.id,
                    "date_from": date(2026, 3, 2),
                    "date_to": date(2026, 3, 2),
                    "period_type": "custom",
                    "attendance_scope": "all",
                    "revision": 1,
                    "employee_ids": [Command.set(self.employee.ids)],
                    "late_grace_minutes": 0,
                    "early_grace_minutes": 0,
                    "shift_merge_minutes": 90,
                }
            )
