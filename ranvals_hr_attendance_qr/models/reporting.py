"""Auditable attendance periods and payroll-ready hour exports.

The reporting engine deliberately prepares hours, not money or payslips.  It
uses Odoo's attendance/overtime records and employee calendars, then freezes a
reviewable snapshot.  Country-specific payroll rules belong in a separate
bridge module.
"""

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .attendance import ADMIN


MAX_REPORT_DAYS = 366
MAX_EMPLOYEE_DAYS = 50_000
MAX_PDF_BATCHES = 20
MAX_PDF_SUMMARY_ROWS = 1_000
MAX_PDF_EXCEPTION_LINES = 500
REPORT_ENGINE_VERSION = "2026.09.27.1"
UTC = pytz.UTC


def _require_report_manager(records):
    if not records.env.user.has_group(ADMIN):
        raise AccessError(_("Bu işlem için Giriş/Çıkış yöneticisi yetkisi gerekir."))


def _utc_aware(value):
    # ``fields.Datetime.to_datetime`` intentionally rejects timezone-aware
    # values.  Odoo's calendar APIs return aware values while ORM fields return
    # naive UTC values, so normalize the two sources without passing an aware
    # datetime back through the ORM converter.
    if not isinstance(value, datetime):
        value = fields.Datetime.to_datetime(value)
    if not value:
        return False
    return UTC.localize(value) if value.tzinfo is None else value.astimezone(UTC)


def _utc_naive(value):
    value = _utc_aware(value)
    return value.replace(tzinfo=None) if value else False


def _local_midnight(tz, value):
    """Return a DST-safe aware local midnight."""
    naive = datetime.combine(value, time.min)
    try:
        return tz.localize(naive, is_dst=None)
    except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError):
        # A handful of historical zones transition at midnight.  Choosing the
        # post-transition side gives a deterministic half-open day boundary.
        return tz.localize(naive, is_dst=False)


def _hours(start, stop):
    return max(0.0, (stop - start).total_seconds() / 3600.0)


def _overlap_hours(start_a, stop_a, start_b, stop_b):
    return _hours(max(start_a, start_b), min(stop_a, stop_b))


def _safe_csv_cell(value):
    """Neutralize spreadsheet formulas while preserving the visible value."""
    if value is None:
        return ""
    text = str(value)
    candidate = text.lstrip(" \t\r\n")
    if candidate.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


class ResCompany(models.Model):
    _inherit = "res.company"

    ranvals_qr_late_grace_minutes = fields.Integer(
        string="QR Raporu Geç Kalma Toleransı (dk)", default=0
    )
    ranvals_qr_early_grace_minutes = fields.Integer(
        string="QR Raporu Erken Çıkma Toleransı (dk)", default=0
    )
    ranvals_qr_shift_merge_minutes = fields.Integer(
        string="QR Raporu Vardiya Birleştirme Aralığı (dk)", default=90
    )

    @api.constrains(
        "ranvals_qr_late_grace_minutes",
        "ranvals_qr_early_grace_minutes",
        "ranvals_qr_shift_merge_minutes",
    )
    def _check_ranvals_qr_report_policy(self):
        for company in self:
            if not 0 <= company.ranvals_qr_late_grace_minutes <= 240:
                raise ValidationError(_("Geç kalma toleransı 0–240 dakika olmalıdır."))
            if not 0 <= company.ranvals_qr_early_grace_minutes <= 240:
                raise ValidationError(_("Erken çıkma toleransı 0–240 dakika olmalıdır."))
            if not 0 <= company.ranvals_qr_shift_merge_minutes <= 240:
                raise ValidationError(_("Vardiya birleştirme aralığı 0–240 dakika olmalıdır."))


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ranvals_qr_late_grace_minutes = fields.Integer(
        related="company_id.ranvals_qr_late_grace_minutes", readonly=False
    )
    ranvals_qr_early_grace_minutes = fields.Integer(
        related="company_id.ranvals_qr_early_grace_minutes", readonly=False
    )
    ranvals_qr_shift_merge_minutes = fields.Integer(
        related="company_id.ranvals_qr_shift_merge_minutes", readonly=False
    )


class RanvalsQRAttendanceReportService(models.AbstractModel):
    """Single Odoo-19 compatibility boundary for attendance calculations."""

    _name = "ranvals.qr.report.service"
    _description = "QR Puantaj Hesaplama Servisi"

    def _employee_timezone(self, employee):
        try:
            return pytz.timezone(employee.sudo()._get_tz() or "UTC")
        except pytz.UnknownTimeZoneError:
            return UTC

    def _employee_timezone_for_date(self, employee, work_date):
        """Return the version/calendar timezone effective on ``work_date``."""
        version = employee.sudo()._get_version(work_date)
        timezone_name = version._get_tz() if version else employee.sudo()._get_tz()
        try:
            return pytz.timezone(timezone_name or "UTC"), version
        except pytz.UnknownTimeZoneError:
            return UTC, version

    def _expected_shifts(self, batch, employee):
        """Return net planned shifts, including cross-midnight segments.

        Odoo's expected-attendance API applies historical versions, contracts,
        public holidays and resource leaves.  Nearby work segments are grouped
        into one shift without adding the intervening break to planned hours.
        """
        tz = self._employee_timezone(employee)
        start = _local_midnight(tz, batch.date_from - timedelta(days=1))
        stop = _local_midnight(tz, batch.date_to + timedelta(days=2))
        intervals = employee.sudo()._get_expected_attendances(start, stop)
        segments = []
        for interval_start, interval_stop, _records in intervals:
            start_utc = _utc_aware(interval_start)
            stop_utc = _utc_aware(interval_stop)
            if start_utc and stop_utc and stop_utc > start_utc:
                segments.append((start_utc, stop_utc))
        segments.sort(key=lambda item: (item[0], item[1]))

        merged = []
        gap = timedelta(minutes=batch.shift_merge_minutes)
        for segment_start, segment_stop in segments:
            can_merge = bool(
                merged
                and segment_start <= merged[-1]["end"] + gap
                and max(merged[-1]["end"], segment_stop) - merged[-1]["start"]
                <= timedelta(hours=24)
            )
            if can_merge:
                merged[-1]["end"] = max(merged[-1]["end"], segment_stop)
                merged[-1]["segments"].append((segment_start, segment_stop))
                merged[-1]["planned_hours"] += _hours(segment_start, segment_stop)
            else:
                merged.append(
                    {
                        "start": segment_start,
                        "end": segment_stop,
                        "segments": [(segment_start, segment_stop)],
                        "planned_hours": _hours(segment_start, segment_stop),
                    }
                )

        result = []
        for index, shift in enumerate(merged):
            provisional_date = shift["start"].astimezone(tz).date()
            historical_tz, version = self._employee_timezone_for_date(
                employee, provisional_date
            )
            work_date = shift["start"].astimezone(historical_tz).date()
            if work_date != provisional_date:
                historical_tz, version = self._employee_timezone_for_date(
                    employee, work_date
                )
            if batch.date_from <= work_date <= batch.date_to:
                calendar = (
                    version.resource_calendar_id
                    if version
                    else employee.resource_calendar_id
                    or employee.company_id.resource_calendar_id
                )
                shift.update(
                    {
                        "index": index,
                        "date": work_date,
                        "timezone": (version._get_tz() if version else False)
                        or historical_tz.zone,
                        "calendar_name": calendar.display_name if calendar else "",
                        "flexible": not calendar or bool(calendar.flexible_hours),
                    }
                )
                result.append(shift)
        return result

    def _attendance_company_domain(self, company):
        return [
            "|",
            ("ranvals_company_snapshot_id", "=", company.id),
            "&",
            ("ranvals_company_snapshot_id", "=", False),
            ("employee_id.company_id", "=", company.id),
        ]

    def _source_attendances(self, batch, employees, bounds):
        domain = [
            ("employee_id", "in", employees.ids),
            ("check_in", "<", bounds[1]),
            "|",
            ("check_out", "=", False),
            ("check_out", ">", bounds[0]),
            *self._attendance_company_domain(batch.company_id),
        ]
        # The batch company and selected public employees were already checked.
        # sudo is necessary for stable historical results after an employee is
        # transferred, because Odoo's standard attendance rule follows the
        # employee's *current* company rather than our immutable snapshot.
        attendances = self.env["hr.attendance"].sudo().search(
            domain, order="employee_id, check_in, id"
        )
        if batch.attendance_scope == "qr":
            attendances = attendances.filtered(
                lambda attendance: attendance.ranvals_qr_in_station_id
                or attendance.ranvals_qr_out_station_id
            )
        return attendances

    def _assign_attendance(self, attendance, shifts, tz):
        start = _utc_aware(attendance.check_in)
        stop = _utc_aware(attendance.check_out) if attendance.check_out else start
        best_shift = False
        best_overlap = 0.0
        for shift in shifts:
            overlap = _overlap_hours(start, max(stop, start), shift["start"], shift["end"])
            if overlap > best_overlap:
                best_shift = shift
                best_overlap = overlap
        if not best_shift:
            for shift in shifts:
                if shift["start"] <= start <= shift["end"]:
                    best_shift = shift
                    break
        if best_shift:
            return best_shift["date"], best_shift["index"]
        return start.astimezone(tz).date(), False

    def _overtime_data(self, batch, employees, attendances):
        lines = self.env["hr.attendance.overtime.line"].sudo().search(
            [
                ("employee_id", "in", employees.ids),
                ("date", ">=", batch.date_from),
                ("date", "<=", batch.date_to),
            ],
            order="employee_id, date, id",
        )
        allowed = {(item.employee_id.id, item.check_in) for item in attendances}
        lines = lines.filtered(
            lambda line: (line.employee_id.id, line.time_start) in allowed
        )

        by_day = defaultdict(
            lambda: {
                "calculated": 0.0,
                "approved": 0.0,
                "pending": 0.0,
                "pending_count": 0,
            }
        )
        approved_by_employee_rate = defaultdict(float)
        for line in lines:
            values = by_day[(line.employee_id.id, line.date)]
            # Odoo also stores absence/undertime rule results in this model as
            # negative durations.  Those are represented by our separately
            # calculated SHORTFALL metric and must never become negative
            # OVERTIME rows.
            values["calculated"] += max(line.duration, 0.0)
            if line.status == "approved":
                approved_duration = max(line.manual_duration, 0.0)
                values["approved"] += approved_duration
                if approved_duration:
                    # ``0.0`` is intentional for an unpaid overtime rule; do
                    # not coerce it to the default paid rate.
                    approved_by_employee_rate[
                        (line.employee_id.id, float(line.amount_rate))
                    ] += approved_duration
            elif line.status == "to_approve":
                values["pending"] += abs(line.manual_duration)
                values["pending_count"] += 1
        return lines, by_day, approved_by_employee_rate

    def _line_values(
        self,
        batch,
        employee,
        work_date,
        shifts,
        assignments,
        overtime,
        generated_at,
    ):
        records = [assignment["attendance"] for assignment in assignments]
        closed = [record for record in records if record.check_out]
        opened = [record for record in records if not record.check_out]
        historical_tz, version = self._employee_timezone_for_date(employee, work_date)
        calendar = (
            version.resource_calendar_id
            if version
            else employee.resource_calendar_id
            or employee.company_id.resource_calendar_id
        )
        flexible = not calendar or bool(calendar.flexible_hours)
        department = (
            version.department_id
            if version and "department_id" in version._fields
            else employee.department_id
        )
        planned_hours = sum(shift["planned_hours"] for shift in shifts)
        worked_hours = sum(record.worked_hours or 0.0 for record in closed)

        scheduled_worked = 0.0
        for record in closed:
            actual_start = _utc_aware(record.check_in)
            actual_stop = _utc_aware(record.check_out)
            for shift in shifts:
                for expected_start, expected_stop in shift["segments"]:
                    scheduled_worked += _overlap_hours(
                        actual_start, actual_stop, expected_start, expected_stop
                    )
        scheduled_worked = min(planned_hours, scheduled_worked)
        if flexible:
            # Odoo deliberately centres flexible-calendar expected intervals at
            # noon as an approximation.  Their clock placement is not a real
            # shift boundary, so only period totals (rebalanced in ``build``)
            # may determine regular/shortfall/outside hours.
            scheduled_worked = min(planned_hours, worked_hours)

        late_raw = late_after_grace = 0.0
        early_raw = early_after_grace = 0.0
        missing_shift_count = 0
        for shift in shifts:
            shift_assignments = [
                assignment
                for assignment in assignments
                if assignment["shift_index"] == shift["index"]
            ]
            if not shift_assignments:
                if not flexible:
                    missing_shift_count += 1
                continue
            if shift["flexible"]:
                continue
            shift_records = [item["attendance"] for item in shift_assignments]
            first_in = min(_utc_aware(record.check_in) for record in shift_records)
            raw_seconds = max(0.0, (first_in - shift["start"]).total_seconds())
            late_raw += raw_seconds / 60.0
            late_after_grace += max(
                0.0, raw_seconds - batch.late_grace_minutes * 60
            ) / 60.0

            if any(not record.check_out for record in shift_records):
                continue
            last_out = max(_utc_aware(record.check_out) for record in shift_records)
            raw_seconds = max(0.0, (shift["end"] - last_out).total_seconds())
            early_raw += raw_seconds / 60.0
            early_after_grace += max(
                0.0, raw_seconds - batch.early_grace_minutes * 60
            ) / 60.0

        first_check_in = min((record.check_in for record in records), default=False)
        last_check_out = max(
            (record.check_out for record in closed), default=False
        )
        qr_count = sum(
            bool(record.ranvals_qr_in_station_id or record.ranvals_qr_out_station_id)
            for record in records
        )
        anomaly_count = sum(
            bool(
                record.check_in > generated_at
                or (
                    record.check_out
                    and (
                        record.check_out > generated_at
                        or record.check_out - record.check_in > timedelta(hours=24)
                    )
                )
            )
            for record in records
        )
        timezone_name = (
            shifts[0]["timezone"] if shifts else historical_tz.zone
        )
        calendar_name = (
            shifts[0]["calendar_name"]
            if shifts
            else (calendar.display_name if calendar else "")
        )
        shortfall = max(planned_hours - scheduled_worked, 0.0)
        outside_schedule = max(worked_hours - scheduled_worked, 0.0)
        missing = bool(planned_hours and not records and not flexible)
        if opened:
            status = "incomplete"
        elif anomaly_count:
            status = "anomaly"
        elif missing:
            status = "missing"
        elif shortfall > 1e-6:
            status = "shortfall"
        elif outside_schedule > 1e-6 or overtime["approved"] > 1e-6:
            status = "overtime"
        elif not planned_hours:
            status = "off_schedule"
        else:
            status = "ok"

        return {
            "batch_id": batch.id,
            "company_id": batch.company_id.id,
            "employee_id": employee.id,
            "employee_name": employee.name,
            "department_name": department.display_name if department else "",
            "work_date": work_date,
            "timezone": timezone_name,
            "calendar_name": calendar_name,
            "is_flexible": flexible,
            "first_check_in": first_check_in,
            "last_check_out": last_check_out,
            "planned_hours": planned_hours,
            "worked_hours": worked_hours,
            "scheduled_worked_hours": scheduled_worked,
            "outside_schedule_hours": outside_schedule,
            "variance_hours": worked_hours - planned_hours,
            "shortfall_hours": shortfall,
            "overtime_calculated_hours": overtime["calculated"],
            "overtime_approved_hours": overtime["approved"],
            "overtime_pending_hours": overtime["pending"],
            "pending_overtime_count": overtime["pending_count"],
            "late_minutes_raw": late_raw,
            "late_minutes": late_after_grace,
            "early_minutes_raw": early_raw,
            "early_minutes": early_after_grace,
            "attendance_count": len(records),
            "qr_attendance_count": qr_count,
            "open_attendance_count": len(opened),
            "missing_shift_count": missing_shift_count,
            "anomaly_count": anomaly_count,
            "status": status,
        }

    def build(self, batch):
        batch.ensure_one()
        _require_report_manager(batch)
        # Attendance administrators can safely select public employee records;
        # private HR data is read only inside this controlled service.
        employees = self.env["hr.employee"].sudo().browse(
            batch.employee_ids.ids
        ).exists().sorted("id")
        if not employees:
            raise UserError(_("Rapor için en az bir personel seçin."))
        day_count = (batch.date_to - batch.date_from).days + 1
        if day_count > MAX_REPORT_DAYS:
            raise ValidationError(
                _("Rapor aralığı en fazla %(days)s gün olabilir.", days=MAX_REPORT_DAYS)
            )
        if day_count * len(employees) > MAX_EMPLOYEE_DAYS:
            raise ValidationError(
                _(
                    "Rapor çok büyük. Personel × gün sayısı %(limit)s sınırını geçemez.",
                    limit=MAX_EMPLOYEE_DAYS,
                )
            )

        shifts_by_employee = {}
        lower_bounds = []
        upper_bounds = []
        for employee in employees:
            tz = self._employee_timezone(employee)
            lower_bounds.append(
                _utc_naive(_local_midnight(tz, batch.date_from - timedelta(days=1)))
            )
            upper_bounds.append(
                _utc_naive(_local_midnight(tz, batch.date_to + timedelta(days=2)))
            )
            shifts_by_employee[employee.id] = self._expected_shifts(batch, employee)
        bounds = (min(lower_bounds), max(upper_bounds))
        attendances = self._source_attendances(batch, employees, bounds)

        assignments_by_employee_date = defaultdict(list)
        for attendance in attendances:
            employee = attendance.employee_id
            provisional_date = _utc_aware(attendance.check_in).astimezone(
                self._employee_timezone(employee)
            ).date()
            attendance_tz, _version = self._employee_timezone_for_date(
                employee, provisional_date
            )
            work_date, shift_index = self._assign_attendance(
                attendance,
                shifts_by_employee[employee.id],
                attendance_tz,
            )
            # An attendance left open before the reporting period still
            # overlaps every later instant.  Keep it visible on the first day
            # so that it blocks review/locking instead of silently vanishing.
            if not attendance.check_out and work_date < batch.date_from:
                work_date, shift_index = batch.date_from, False
            if batch.date_from <= work_date <= batch.date_to:
                assignments_by_employee_date[(employee.id, work_date)].append(
                    {"attendance": attendance, "shift_index": shift_index}
                )

        overtime_lines, overtime_by_day, approved_by_employee_rate = (
            self._overtime_data(batch, employees, attendances)
        )
        shifts_by_employee_date = defaultdict(list)
        for employee_id, shifts in shifts_by_employee.items():
            for shift in shifts:
                shifts_by_employee_date[(employee_id, shift["date"])].append(shift)

        generated_at = fields.Datetime.now()
        day_values = []
        for employee in employees:
            dates = {
                work_date
                for (employee_id, work_date) in shifts_by_employee_date
                if employee_id == employee.id
            }
            dates.update(
                work_date
                for (employee_id, work_date) in assignments_by_employee_date
                if employee_id == employee.id
            )
            dates.update(
                work_date
                for (employee_id, work_date) in overtime_by_day
                if employee_id == employee.id
            )
            for work_date in sorted(dates):
                if not batch.date_from <= work_date <= batch.date_to:
                    continue
                day_values.append(
                    self._line_values(
                        batch,
                        employee,
                        work_date,
                        shifts_by_employee_date[(employee.id, work_date)],
                        assignments_by_employee_date[(employee.id, work_date)],
                        overtime_by_day[(employee.id, work_date)],
                        generated_at,
                    )
                )

        # Flexible calendars define a period quota, not real daily start/end
        # boundaries.  Reconcile their hours at period level so a worker who
        # completes the weekly total on different days is not falsely marked
        # both short and outside schedule.
        for employee in employees:
            flexible_values = [
                values
                for values in day_values
                if values["employee_id"] == employee.id and values["is_flexible"]
            ]
            if not flexible_values:
                continue
            planned_total = sum(values["planned_hours"] for values in flexible_values)
            worked_total = sum(values["worked_hours"] for values in flexible_values)
            regular_total = min(planned_total, worked_total)
            shortfall_total = max(planned_total - worked_total, 0.0)
            outside_total = max(worked_total - planned_total, 0.0)
            for values in flexible_values:
                worked_share = (
                    values["worked_hours"] / worked_total if worked_total else 0.0
                )
                planned_share = (
                    values["planned_hours"] / planned_total if planned_total else 0.0
                )
                values["scheduled_worked_hours"] = regular_total * worked_share
                values["outside_schedule_hours"] = outside_total * worked_share
                values["shortfall_hours"] = shortfall_total * planned_share
                values["missing_shift_count"] = 0
                values["late_minutes_raw"] = 0.0
                values["late_minutes"] = 0.0
                values["early_minutes_raw"] = 0.0
                values["early_minutes"] = 0.0
                if values["open_attendance_count"]:
                    values["status"] = "incomplete"
                elif values["anomaly_count"]:
                    values["status"] = "anomaly"
                elif values["shortfall_hours"] > 1e-6:
                    values["status"] = "shortfall"
                elif (
                    values["outside_schedule_hours"] > 1e-6
                    or values["overtime_approved_hours"] > 1e-6
                ):
                    values["status"] = "overtime"
                elif not values["planned_hours"]:
                    values["status"] = "off_schedule"
                else:
                    values["status"] = "ok"

        totals_by_employee = defaultdict(
            lambda: {"regular": 0.0, "shortfall": 0.0}
        )
        for values in day_values:
            target = totals_by_employee[values["employee_id"]]
            target["regular"] += values["scheduled_worked_hours"]
            target["shortfall"] += values["shortfall_hours"]

        payroll_values = []
        for employee in employees:
            totals = totals_by_employee[employee.id]
            department_names = sorted(
                {
                    values["department_name"]
                    for values in day_values
                    if values["employee_id"] == employee.id
                    and values["department_name"]
                }
            )
            common = {
                "batch_id": batch.id,
                "company_id": batch.company_id.id,
                "employee_id": employee.id,
                "employee_name": employee.name,
                "department_name": " / ".join(department_names),
            }
            if abs(totals["regular"]) > 1e-6:
                payroll_values.append(
                    {**common, "code": "REGULAR", "hours": totals["regular"], "rate": 1.0}
                )
            if abs(totals["shortfall"]) > 1e-6:
                payroll_values.append(
                    {**common, "code": "UNDERTIME", "hours": totals["shortfall"], "rate": 1.0}
                )
            for (employee_id, rate), overtime_hours in sorted(
                approved_by_employee_rate.items()
            ):
                if employee_id == employee.id and abs(overtime_hours) > 1e-6:
                    payroll_values.append(
                        {
                            **common,
                            "code": "OVERTIME",
                            "hours": overtime_hours,
                            "rate": rate,
                        }
                    )

        source_payload = {
            "engine": REPORT_ENGINE_VERSION,
            "company": batch.company_id.id,
            "dates": [str(batch.date_from), str(batch.date_to)],
            "scope": batch.attendance_scope,
            "policy": [
                batch.late_grace_minutes,
                batch.early_grace_minutes,
                batch.shift_merge_minutes,
            ],
            "employees": [
                [employee.id, str(employee.write_date or "")]
                for employee in employees
            ],
            "expected_shifts": [
                [
                    employee_id,
                    str(shift["date"]),
                    shift["timezone"],
                    shift["calendar_name"],
                    bool(shift["flexible"]),
                    [
                        [start.isoformat(), stop.isoformat()]
                        for start, stop in shift["segments"]
                    ],
                ]
                for employee_id, shifts in sorted(shifts_by_employee.items())
                for shift in shifts
            ],
            "attendances": [
                [
                    attendance.id,
                    str(attendance.write_date or ""),
                    str(attendance.check_in or ""),
                    str(attendance.check_out or ""),
                ]
                for attendance in attendances
            ],
            "overtimes": [
                [
                    line.id,
                    str(line.write_date or ""),
                    line.status,
                    line.manual_duration,
                    line.amount_rate,
                ]
                for line in overtime_lines
            ],
        }
        source_hash = hashlib.sha256(
            json.dumps(source_payload, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        return {
            "generated_at": generated_at,
            "day_values": day_values,
            "payroll_values": payroll_values,
            "source_hash": source_hash,
        }


class RanvalsQRAttendanceReportBatch(models.Model):
    _name = "ranvals.qr.report.batch"
    _description = "QR Puantaj Dönemi"
    _order = "date_from desc, company_id, revision desc, id desc"
    _check_company_auto = True

    _audit_control_fields = {
        "state",
        "generated_by_id",
        "generated_at",
        "reviewed_by_id",
        "reviewed_at",
        "locked_by_id",
        "locked_at",
        "source_hash",
        "day_line_ids",
        "payroll_line_ids",
    }

    name = fields.Char(required=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    date_from = fields.Date(required=True, index=True, readonly=True)
    date_to = fields.Date(required=True, index=True, readonly=True)
    period_type = fields.Selection(
        [("week", "Haftalık"), ("month", "Aylık"), ("custom", "Özel Aralık")],
        required=True,
        readonly=True,
    )
    attendance_scope = fields.Selection(
        [("all", "Tüm Mesai Kanalları"), ("qr", "Yalnız QR Kayıtları")],
        required=True,
        readonly=True,
        default="all",
    )
    revision = fields.Integer(required=True, readonly=True, default=1)
    state = fields.Selection(
        [("draft", "Taslak"), ("review", "İncelendi"), ("locked", "Kilitli")],
        required=True,
        default="draft",
        index=True,
    )
    employee_ids = fields.Many2many(
        "hr.employee.public",
        "ranvals_qr_report_batch_employee_rel",
        "batch_id",
        "employee_id",
        required=True,
        readonly=True,
    )
    late_grace_minutes = fields.Integer(required=True, readonly=True)
    early_grace_minutes = fields.Integer(required=True, readonly=True)
    shift_merge_minutes = fields.Integer(required=True, readonly=True)
    generated_by_id = fields.Many2one("res.users", readonly=True)
    generated_at = fields.Datetime(readonly=True)
    reviewed_by_id = fields.Many2one("res.users", readonly=True)
    reviewed_at = fields.Datetime(readonly=True)
    locked_by_id = fields.Many2one("res.users", readonly=True)
    locked_at = fields.Datetime(readonly=True)
    source_hash = fields.Char(readonly=True, groups=ADMIN)
    day_line_ids = fields.One2many(
        "ranvals.qr.report.day", "batch_id", readonly=True
    )
    payroll_line_ids = fields.One2many(
        "ranvals.qr.report.payroll.line", "batch_id", readonly=True
    )
    planned_hours = fields.Float(compute="_compute_totals", store=True)
    worked_hours = fields.Float(compute="_compute_totals", store=True)
    scheduled_worked_hours = fields.Float(compute="_compute_totals", store=True)
    outside_schedule_hours = fields.Float(compute="_compute_totals", store=True)
    shortfall_hours = fields.Float(compute="_compute_totals", store=True)
    approved_overtime_hours = fields.Float(compute="_compute_totals", store=True)
    late_minutes = fields.Float(compute="_compute_totals", store=True)
    early_minutes = fields.Float(compute="_compute_totals", store=True)
    missing_day_count = fields.Integer(compute="_compute_totals", store=True)
    open_attendance_count = fields.Integer(compute="_compute_totals", store=True)
    pending_overtime_count = fields.Integer(compute="_compute_totals", store=True)
    anomaly_count = fields.Integer(compute="_compute_totals", store=True)
    blocker_count = fields.Integer(compute="_compute_totals", store=True)
    payroll_ready = fields.Boolean(
        string="Puantaj Girdisi Dışa Aktarımına Hazır",
        compute="_compute_totals",
        store=True,
        help="Açık/anormal kayıt ve bekleyen fazla mesai onayı bulunmayan tüm-kanal snapshot'ı. Maaş veya bordro fişi hazır olduğu anlamına gelmez.",
    )

    _period_revision_unique = models.Constraint(
        "UNIQUE(company_id, date_from, date_to, attendance_scope, revision)",
        "Aynı dönem, kapsam ve revizyon zaten mevcut.",
    )

    @api.depends(
        "attendance_scope",
        "generated_at",
        "day_line_ids.planned_hours",
        "day_line_ids.worked_hours",
        "day_line_ids.scheduled_worked_hours",
        "day_line_ids.outside_schedule_hours",
        "day_line_ids.shortfall_hours",
        "day_line_ids.overtime_approved_hours",
        "day_line_ids.late_minutes",
        "day_line_ids.early_minutes",
        "day_line_ids.status",
        "day_line_ids.open_attendance_count",
        "day_line_ids.pending_overtime_count",
        "day_line_ids.anomaly_count",
    )
    def _compute_totals(self):
        for batch in self:
            lines = batch.day_line_ids
            batch.planned_hours = sum(lines.mapped("planned_hours"))
            batch.worked_hours = sum(lines.mapped("worked_hours"))
            batch.scheduled_worked_hours = sum(lines.mapped("scheduled_worked_hours"))
            batch.outside_schedule_hours = sum(lines.mapped("outside_schedule_hours"))
            batch.shortfall_hours = sum(lines.mapped("shortfall_hours"))
            batch.approved_overtime_hours = sum(
                lines.mapped("overtime_approved_hours")
            )
            batch.late_minutes = sum(lines.mapped("late_minutes"))
            batch.early_minutes = sum(lines.mapped("early_minutes"))
            batch.missing_day_count = len(lines.filtered(lambda line: line.status == "missing"))
            batch.open_attendance_count = sum(lines.mapped("open_attendance_count"))
            batch.pending_overtime_count = sum(lines.mapped("pending_overtime_count"))
            batch.anomaly_count = sum(lines.mapped("anomaly_count"))
            batch.blocker_count = (
                batch.open_attendance_count
                + batch.pending_overtime_count
                + batch.anomaly_count
                + (1 if batch.attendance_scope != "all" else 0)
            )
            batch.payroll_ready = not batch.blocker_count and bool(batch.generated_at)

    @api.constrains("date_from", "date_to", "employee_ids", "company_id")
    def _check_period(self):
        for batch in self:
            if batch.date_from > batch.date_to:
                raise ValidationError(_("Başlangıç tarihi bitiş tarihinden sonra olamaz."))
            if (batch.date_to - batch.date_from).days + 1 > MAX_REPORT_DAYS:
                raise ValidationError(
                    _("Rapor aralığı en fazla %(days)s gün olabilir.", days=MAX_REPORT_DAYS)
                )
            for employee in batch.employee_ids:
                if employee.company_id == batch.company_id:
                    continue
                historical_version = self.env["hr.version"].sudo().with_context(
                    active_test=False
                ).search_count(
                    [
                        ("employee_id", "=", employee.id),
                        ("company_id", "=", batch.company_id.id),
                        ("date_start", "<=", batch.date_to),
                        "|",
                        ("date_end", "=", False),
                        ("date_end", ">=", batch.date_from),
                    ],
                    limit=1,
                )
                if not historical_version:
                    raise ValidationError(
                        _("Seçilen personeller rapor döneminde rapor şirketine ait olmalıdır.")
                    )

    @api.model_create_multi
    def create(self, vals_list):
        _require_report_manager(self)
        for vals in vals_list:
            forged = self._audit_control_fields.intersection(vals)
            if forged:
                raise AccessError(
                    _("Denetim alanları doğrudan oluşturulamaz: %(fields)s", fields=", ".join(sorted(forged)))
                )
            vals["state"] = "draft"
            if vals.get("date_from") and vals.get("date_to"):
                vals["name"] = self._period_name(
                    fields.Date.to_date(vals["date_from"]),
                    fields.Date.to_date(vals["date_to"]),
                    vals.get("revision", 1),
                    vals.get("attendance_scope", "all"),
                )
        return super().create(vals_list)

    def write(self, vals):
        _require_report_manager(self)
        if any(batch.state == "locked" for batch in self):
            raise UserError(_("Kilitli puantaj dönemi değiştirilemez; yeni revizyon oluşturun."))
        protected = {
            "name",
            "company_id",
            "date_from",
            "date_to",
            "period_type",
            "attendance_scope",
            "revision",
            "employee_ids",
            "late_grace_minutes",
            "early_grace_minutes",
            "shift_merge_minutes",
        } | self._audit_control_fields
        if protected.intersection(vals):
            raise AccessError(
                _("Dönem kapsamı ve denetim alanları doğrudan değiştirilemez; ilgili işlem düğmesini veya yeni revizyonu kullanın.")
            )
        return super().write(vals)

    def unlink(self):
        _require_report_manager(self)
        if any(batch.state == "locked" for batch in self):
            raise UserError(_("Kilitli puantaj dönemi silinemez."))
        return super().unlink()

    def action_calculate(self):
        _require_report_manager(self)
        service = self.env["ranvals.qr.report.service"]
        for batch in self:
            if batch.state != "draft":
                raise UserError(_("Yalnız taslak dönem yeniden hesaplanabilir."))
            result = service.build(batch)
            batch.day_line_ids.sudo().unlink()
            batch.payroll_line_ids.sudo().unlink()
            if result["day_values"]:
                self.env["ranvals.qr.report.day"].sudo().create(result["day_values"])
            if result["payroll_values"]:
                self.env["ranvals.qr.report.payroll.line"].sudo().create(
                    result["payroll_values"]
                )
            batch.invalidate_recordset()
            super(RanvalsQRAttendanceReportBatch, batch).write(
                {
                    "generated_at": result["generated_at"],
                    "generated_by_id": self.env.user.id,
                    "source_hash": result["source_hash"],
                }
            )
        return True

    def action_mark_reviewed(self):
        _require_report_manager(self)
        for batch in self:
            if batch.state != "draft" or not batch.generated_at:
                raise UserError(_("Önce taslak dönemi hesaplayın."))
            super(RanvalsQRAttendanceReportBatch, batch).write(
                {
                    "state": "review",
                    "reviewed_by_id": self.env.user.id,
                    "reviewed_at": fields.Datetime.now(),
                }
            )
        return True

    def action_reset_draft(self):
        _require_report_manager(self)
        for batch in self:
            if batch.state != "review":
                raise UserError(_("Yalnız incelenmiş dönem taslağa alınabilir."))
            super(RanvalsQRAttendanceReportBatch, batch).write(
                {"state": "draft", "reviewed_by_id": False, "reviewed_at": False}
            )
        return True

    def action_lock(self):
        _require_report_manager(self)
        for batch in self:
            if batch.state != "review":
                raise UserError(_("Dönem kilitlenmeden önce incelendi olarak işaretlenmelidir."))
            if not batch.payroll_ready:
                raise UserError(
                    _(
                        "Dönem kilitlenemez. Açık kayıtları, bekleyen fazla mesai onaylarını, "
                        "anormal kayıtları ve rapor kapsamını kontrol edin."
                    )
                )
            current = self.env["ranvals.qr.report.service"].build(batch)
            if current["source_hash"] != batch.source_hash:
                raise UserError(
                    _(
                        "Mesai, onay, takvim veya izin kaynakları hesaplamadan sonra değişti. "
                        "Dönemi taslağa alın, yeniden hesaplayın ve tekrar inceleyin."
                    )
                )
            super(RanvalsQRAttendanceReportBatch, batch).write(
                {
                    "state": "locked",
                    "locked_by_id": self.env.user.id,
                    "locked_at": fields.Datetime.now(),
                }
            )
        return True

    def action_new_revision(self):
        self.ensure_one()
        _require_report_manager(self)
        self.env.cr.execute(
            "SELECT id FROM res_company WHERE id = %s FOR UPDATE", [self.company_id.id]
        )
        latest = self.search(
            [
                ("company_id", "=", self.company_id.id),
                ("date_from", "=", self.date_from),
                ("date_to", "=", self.date_to),
                ("attendance_scope", "=", self.attendance_scope),
            ],
            order="revision desc",
            limit=1,
        )
        revision = (latest.revision or 0) + 1
        new_batch = self.create(
            {
                "name": self._period_name(
                    self.date_from, self.date_to, revision, self.attendance_scope
                ),
                "company_id": self.company_id.id,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "period_type": self.period_type,
                "attendance_scope": self.attendance_scope,
                "revision": revision,
                "employee_ids": [(6, 0, self.employee_ids.ids)],
                "late_grace_minutes": self.late_grace_minutes,
                "early_grace_minutes": self.early_grace_minutes,
                "shift_merge_minutes": self.shift_merge_minutes,
            }
        )
        new_batch.action_calculate()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": new_batch.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _period_name(self, date_from, date_to, revision, scope):
        suffix = _("QR Denetimi") if scope == "qr" else _("Puantaj")
        return _(
            "%(suffix)s %(date_from)s – %(date_to)s / R%(revision)s",
            suffix=suffix,
            date_from=date_from,
            date_to=date_to,
            revision=revision,
        )

    def _summary_rows(self):
        self.ensure_one()
        snapshot_by_employee = {}
        for line in self.day_line_ids.sorted(
            key=lambda item: (item.work_date, item.id)
        ):
            snapshot_by_employee.setdefault(
                line.employee_id.id,
                (line.employee_name, line.department_name),
            )
        for line in self.payroll_line_ids.sorted("id"):
            snapshot_by_employee.setdefault(
                line.employee_id.id,
                (line.employee_name, line.department_name),
            )
        rows = {
            employee.id: {
                "employee_id": employee.id,
                "employee_name": snapshot_by_employee.get(
                    employee.id, (employee.name, "")
                )[0],
                "department_name": snapshot_by_employee.get(
                    employee.id,
                    (
                        employee.name,
                        employee.department_id.display_name
                        if employee.department_id
                        else "",
                    ),
                )[1],
                "planned": 0.0,
                "worked": 0.0,
                "scheduled_worked": 0.0,
                "outside": 0.0,
                "shortfall": 0.0,
                "approved_overtime": 0.0,
                "late": 0.0,
                "early": 0.0,
                "missing_days": 0,
                "open_records": 0,
                "pending_overtimes": 0,
                "anomalies": 0,
            }
            for employee in self.employee_ids
        }
        for line in self.day_line_ids:
            target = rows[line.employee_id.id]
            target["planned"] += line.planned_hours
            target["worked"] += line.worked_hours
            target["scheduled_worked"] += line.scheduled_worked_hours
            target["outside"] += line.outside_schedule_hours
            target["shortfall"] += line.shortfall_hours
            target["approved_overtime"] += line.overtime_approved_hours
            target["late"] += line.late_minutes
            target["early"] += line.early_minutes
            target["missing_days"] += int(line.status == "missing")
            target["open_records"] += line.open_attendance_count
            target["pending_overtimes"] += line.pending_overtime_count
            target["anomalies"] += line.anomaly_count
        return [rows[key] for key in sorted(rows, key=lambda item: rows[item]["employee_name"])]

    def _local_datetime_text(self, value, timezone_name):
        if not value:
            return ""
        try:
            tz = pytz.timezone(timezone_name or "UTC")
        except pytz.UnknownTimeZoneError:
            tz = UTC
        return _utc_aware(value).astimezone(tz).isoformat(timespec="minutes")

    def _csv_content(self, kind):
        self.ensure_one()
        _require_report_manager(self)
        if kind not in {"detail", "payroll"}:
            raise UserError(_("Geçersiz dışa aktarma türü."))
        if not self.generated_at:
            raise UserError(_("Önce raporu hesaplayın."))
        if kind == "payroll" and (self.state != "locked" or not self.payroll_ready):
            raise UserError(_("Bordro hazırlık CSV'si yalnız kilitli ve temiz dönemden alınabilir."))

        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        if kind == "detail":
            writer.writerow(
                [
                    "Employee ID",
                    "Employee",
                    "Department",
                    "Work Date",
                    "Timezone",
                    "First Check In",
                    "Last Check Out",
                    "Planned Hours",
                    "Worked Hours",
                    "Scheduled Worked Hours",
                    "Outside Schedule Hours",
                    "Variance Hours",
                    "Shortfall Hours",
                    "Calculated Overtime Hours",
                    "Approved Overtime Hours",
                    "Pending Overtime Hours",
                    "Late Minutes",
                    "Early Leave Minutes",
                    "Attendance Count",
                    "QR Attendance Count",
                    "Open Records",
                    "Status",
                ]
            )
            for line in self.day_line_ids.sorted(
                key=lambda item: (item.employee_name, item.work_date, item.id)
            ):
                writer.writerow(
                    [
                        line.employee_id.id,
                        _safe_csv_cell(line.employee_name),
                        _safe_csv_cell(line.department_name),
                        line.work_date.isoformat(),
                        line.timezone,
                        self._local_datetime_text(line.first_check_in, line.timezone),
                        self._local_datetime_text(line.last_check_out, line.timezone),
                        f"{line.planned_hours:.4f}",
                        f"{line.worked_hours:.4f}",
                        f"{line.scheduled_worked_hours:.4f}",
                        f"{line.outside_schedule_hours:.4f}",
                        f"{line.variance_hours:.4f}",
                        f"{line.shortfall_hours:.4f}",
                        f"{line.overtime_calculated_hours:.4f}",
                        f"{line.overtime_approved_hours:.4f}",
                        f"{line.overtime_pending_hours:.4f}",
                        f"{line.late_minutes:.2f}",
                        f"{line.early_minutes:.2f}",
                        line.attendance_count,
                        line.qr_attendance_count,
                        line.open_attendance_count,
                        line.status,
                    ]
                )
        else:
            writer.writerow(
                [
                    "Period",
                    "Revision",
                    "Employee ID",
                    "Employee",
                    "Department",
                    "Payroll Code",
                    "Hours",
                    "Rate",
                    "Weighted Hours",
                    "Source Hash",
                ]
            )
            period = f"{self.date_from.isoformat()}/{self.date_to.isoformat()}"
            for line in self.payroll_line_ids.sorted(
                key=lambda item: (item.employee_name, item.code, item.rate, item.id)
            ):
                writer.writerow(
                    [
                        period,
                        self.revision,
                        line.employee_id.id,
                        _safe_csv_cell(line.employee_name),
                        _safe_csv_cell(line.department_name),
                        line.code,
                        f"{line.hours:.4f}",
                        f"{line.rate:.4f}",
                        f"{line.weighted_hours:.4f}",
                        self.source_hash,
                    ]
                )
        return ("\ufeff" + stream.getvalue()).encode("utf-8")

    def action_download_detail_csv(self):
        self.ensure_one()
        _require_report_manager(self)
        return {
            "type": "ir.actions.act_url",
            "url": f"/ranvals/qr-attendance/report/{self.id}/detail.csv",
            "target": "self",
        }

    def action_download_payroll_csv(self):
        self.ensure_one()
        _require_report_manager(self)
        if self.state != "locked" or not self.payroll_ready:
            raise UserError(_("Önce temiz dönemi inceleyip kilitleyin."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/ranvals/qr-attendance/report/{self.id}/payroll.csv",
            "target": "self",
        }

    def action_print_pdf(self):
        self.ensure_one()
        _require_report_manager(self)
        if not self.generated_at:
            raise UserError(_("Önce raporu hesaplayın."))
        return self.env.ref("ranvals_hr_attendance_qr.action_report_attendance_batch").report_action(self)


class RanvalsQRAttendanceReportDay(models.Model):
    _name = "ranvals.qr.report.day"
    _description = "QR Puantaj Gün Satırı"
    _order = "employee_name, work_date, id"
    _check_company_auto = True

    batch_id = fields.Many2one(
        "ranvals.qr.report.batch", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    employee_id = fields.Many2one(
        "hr.employee", required=True, index=True, ondelete="restrict"
    )
    employee_name = fields.Char(required=True, readonly=True)
    department_name = fields.Char(readonly=True)
    work_date = fields.Date(required=True, index=True, readonly=True)
    timezone = fields.Char(required=True, readonly=True)
    calendar_name = fields.Char(readonly=True)
    is_flexible = fields.Boolean(readonly=True)
    first_check_in = fields.Datetime(readonly=True)
    last_check_out = fields.Datetime(readonly=True)
    planned_hours = fields.Float(readonly=True, digits=(16, 4))
    worked_hours = fields.Float(readonly=True, digits=(16, 4))
    scheduled_worked_hours = fields.Float(readonly=True, digits=(16, 4))
    outside_schedule_hours = fields.Float(readonly=True, digits=(16, 4))
    variance_hours = fields.Float(readonly=True, digits=(16, 4))
    shortfall_hours = fields.Float(readonly=True, digits=(16, 4))
    overtime_calculated_hours = fields.Float(readonly=True, digits=(16, 4))
    overtime_approved_hours = fields.Float(readonly=True, digits=(16, 4))
    overtime_pending_hours = fields.Float(readonly=True, digits=(16, 4))
    pending_overtime_count = fields.Integer(readonly=True)
    late_minutes_raw = fields.Float(readonly=True, digits=(16, 2))
    late_minutes = fields.Float(readonly=True, digits=(16, 2))
    early_minutes_raw = fields.Float(readonly=True, digits=(16, 2))
    early_minutes = fields.Float(readonly=True, digits=(16, 2))
    attendance_count = fields.Integer(readonly=True)
    qr_attendance_count = fields.Integer(readonly=True)
    open_attendance_count = fields.Integer(readonly=True)
    missing_shift_count = fields.Integer(readonly=True)
    anomaly_count = fields.Integer(readonly=True)
    status = fields.Selection(
        [
            ("ok", "Uygun"),
            ("missing", "Devamsız"),
            ("incomplete", "Açık Çıkış"),
            ("anomaly", "Kontrol Gerekli"),
            ("shortfall", "Eksik Süre"),
            ("overtime", "Fazla/Vardiya Dışı"),
            ("off_schedule", "Plansız Gün"),
        ],
        required=True,
        readonly=True,
        index=True,
    )

    _employee_day_unique = models.Constraint(
        "UNIQUE(batch_id, employee_id, work_date)",
        "Bir dönemde personel ve iş günü yalnız bir kez bulunabilir.",
    )


class RanvalsQRAttendancePayrollLine(models.Model):
    _name = "ranvals.qr.report.payroll.line"
    _description = "QR Bordro Hazırlık Satırı"
    _order = "employee_name, code, rate, id"
    _check_company_auto = True

    batch_id = fields.Many2one(
        "ranvals.qr.report.batch", required=True, index=True, ondelete="cascade"
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    employee_id = fields.Many2one(
        "hr.employee", required=True, index=True, ondelete="restrict"
    )
    employee_name = fields.Char(required=True, readonly=True)
    department_name = fields.Char(readonly=True)
    code = fields.Selection(
        [("REGULAR", "Plan İçinde Çalışma"), ("OVERTIME", "Onaylı Fazla Mesai"), ("UNDERTIME", "Eksik Süre")],
        required=True,
        readonly=True,
        index=True,
    )
    hours = fields.Float(required=True, readonly=True, digits=(16, 4))
    rate = fields.Float(required=True, readonly=True, digits=(16, 4), default=1.0)
    weighted_hours = fields.Float(
        compute="_compute_weighted_hours", store=True, digits=(16, 4)
    )

    @api.depends("hours", "rate")
    def _compute_weighted_hours(self):
        for line in self:
            line.weighted_hours = line.hours * line.rate


class RanvalsQRAttendanceReportWizard(models.TransientModel):
    _name = "ranvals.qr.report.wizard"
    _description = "QR Puantaj Raporu Oluştur"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    period_type = fields.Selection(
        [("week", "Haftalık"), ("month", "Aylık"), ("custom", "Özel Aralık")],
        required=True,
        default="week",
    )
    anchor_date = fields.Date(required=True, default=fields.Date.context_today)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    attendance_scope = fields.Selection(
        [("all", "Tüm Mesai Kanalları (bordro için)"), ("qr", "Yalnız QR Kullanım Denetimi")],
        required=True,
        default="all",
    )
    department_id = fields.Many2one("hr.department")
    employee_ids = fields.Many2many("hr.employee.public", string="Personeller")

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        anchor = fields.Date.to_date(values.get("anchor_date")) or fields.Date.context_today(self)
        values.update(self._period_dates("week", anchor))
        return values

    @api.onchange("period_type", "anchor_date")
    def _onchange_period(self):
        if self.period_type != "custom" and self.anchor_date:
            values = self._period_dates(self.period_type, self.anchor_date)
            self.date_from = values["date_from"]
            self.date_to = values["date_to"]

    @api.model
    def _period_dates(self, period_type, anchor):
        anchor = fields.Date.to_date(anchor)
        if period_type == "month":
            start = anchor.replace(day=1)
            return {"date_from": start, "date_to": start + relativedelta(months=1, days=-1)}
        start = anchor - timedelta(days=anchor.weekday())
        return {"date_from": start, "date_to": start + timedelta(days=6)}

    def action_generate(self):
        self.ensure_one()
        _require_report_manager(self)
        if self.company_id not in self.env.companies:
            raise AccessError(_("Bu şirket için rapor oluşturma yetkiniz yok."))
        if self.date_from > self.date_to:
            raise ValidationError(_("Başlangıç tarihi bitiş tarihinden sonra olamaz."))
        employees = self.employee_ids
        if not employees:
            domain = [("company_id", "=", self.company_id.id), ("active", "=", True)]
            if self.department_id:
                domain.append(("department_id", "child_of", self.department_id.id))
            private_employees = self.env["hr.employee"].sudo().with_context(
                active_test=False
            ).search(domain, order="name, id")
            # Add archived employees whose contract/version overlapped the
            # requested historical period. Public employee ids mirror private
            # employee ids in Odoo 19's SQL view.
            version_domain = [
                ("company_id", "=", self.company_id.id),
                ("employee_id", "!=", False),
                ("date_start", "<=", self.date_to),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", self.date_from),
            ]
            if self.department_id:
                version_domain.append(
                    ("department_id", "child_of", self.department_id.id)
                )
            historical_ids = self.env["hr.version"].sudo().with_context(
                active_test=False
            ).search(version_domain).employee_id.ids
            employee_ids = sorted(set(private_employees.ids) | set(historical_ids))
            employees = self.env["hr.employee.public"].with_context(
                active_test=False
            ).browse(employee_ids).exists().sorted("name")
        if not employees:
            raise UserError(_("Seçilen dönem ve kapsamda personel bulunamadı."))

        self.env.cr.execute(
            "SELECT id FROM res_company WHERE id = %s FOR UPDATE", [self.company_id.id]
        )
        batch_model = self.env["ranvals.qr.report.batch"]
        latest = batch_model.search(
            [
                ("company_id", "=", self.company_id.id),
                ("date_from", "=", self.date_from),
                ("date_to", "=", self.date_to),
                ("attendance_scope", "=", self.attendance_scope),
            ],
            order="revision desc",
            limit=1,
        )
        revision = (latest.revision or 0) + 1
        batch = batch_model.create(
            {
                "name": batch_model._period_name(
                    self.date_from, self.date_to, revision, self.attendance_scope
                ),
                "company_id": self.company_id.id,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "period_type": self.period_type,
                "attendance_scope": self.attendance_scope,
                "revision": revision,
                "employee_ids": [(6, 0, employees.ids)],
                "late_grace_minutes": self.company_id.ranvals_qr_late_grace_minutes,
                "early_grace_minutes": self.company_id.ranvals_qr_early_grace_minutes,
                "shift_merge_minutes": self.company_id.ranvals_qr_shift_merge_minutes,
            }
        )
        batch.action_calculate()
        return {
            "type": "ir.actions.act_window",
            "res_model": batch._name,
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }


class RanvalsQRAttendanceBatchReport(models.AbstractModel):
    _name = "report.ranvals_hr_attendance_qr.report_attendance_batch"
    _description = "QR Puantaj PDF Sağlayıcısı"

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env["ranvals.qr.report.batch"].browse(docids).exists()
        docs.check_access("read")
        _require_report_manager(docs)
        if len(docs) > MAX_PDF_BATCHES:
            raise UserError(
                _("Tek PDF işleminde en fazla %(count)s dönem yazdırılabilir.", count=MAX_PDF_BATCHES)
            )
        if any(not batch.generated_at for batch in docs):
            raise UserError(_("PDF için önce tüm dönemleri hesaplayın."))
        exception_lines = {}
        exception_counts = {}
        summaries = {}
        summary_counts = {}
        for batch in docs:
            summary_rows = batch._summary_rows()
            summary_counts[batch.id] = len(summary_rows)
            summaries[batch.id] = summary_rows[:MAX_PDF_SUMMARY_ROWS]
            lines = batch.day_line_ids.filtered(lambda line: line.status != "ok")
            exception_counts[batch.id] = len(lines)
            exception_lines[batch.id] = lines[:MAX_PDF_EXCEPTION_LINES]
        return {
            "doc_ids": docs.ids,
            "doc_model": docs._name,
            "docs": docs,
            "summaries": summaries,
            "summary_counts": summary_counts,
            "summary_limit": MAX_PDF_SUMMARY_ROWS,
            "exception_lines": exception_lines,
            "exception_counts": exception_counts,
            "exception_limit": MAX_PDF_EXCEPTION_LINES,
        }
