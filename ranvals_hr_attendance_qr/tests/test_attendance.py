"""Odoo registry tests for the QR attendance transaction."""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user

from ..models import attendance as attendance_model


@tagged("post_install", "-at_install", "ranvals_qr")
class TestRanvalsQRAttendance(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.station = cls.env["ranvals.qr.station"].create(
            {
                "name": "QR TEST — NOT LIVE",
                "brand_name": "Ranvals Test",
                "base_url": "https://example.test",
                "allowed_networks": "8.8.8.8/32",
                "enabled": True,
                "cooldown_seconds": 10,
            }
        )
        cls.employee = cls.env["hr.employee"].create(
            {"name": "QR Test Employee", "company_id": cls.env.company.id}
        )
        cls.credential = cls.env["ranvals.qr.credential"].create(
            {
                "employee_id": cls.employee.id,
                "company_id": cls.env.company.id,
                "code": "QRTEST01",
            }
        )
        cls.credential._set_pin("58420936")
        cls.clock = fields.Datetime.now().replace(microsecond=0)
        cls.manager = new_test_user(
            cls.env,
            login="ranvals_qr_manager_test",
            groups="hr_attendance.group_hr_attendance_manager",
            lang="en_US",
        )

    def _login(self, *, remember=True):
        return self.station._authenticate_pin(
            "QRTEST01", "58420936", "8.8.8.8", remember=remember
        )

    def _intent(self, credential=None):
        return self.station._public_state(credential or self.credential)["intent"]

    def _expect_access_error_without_rollback(self, callback):
        """Catch a business denial without TransactionCase's rollback savepoint."""
        try:
            callback()
        except AccessError:
            return
        self.fail("AccessError not raised")

    def test_authentication_does_not_create_odoo_user(self):
        before = self.env["res.users"].with_context(active_test=False).search_count([])
        self._login()
        after = self.env["res.users"].with_context(active_test=False).search_count([])
        self.assertEqual(before, after)
        self.assertFalse(self.employee.user_id)

    def test_network_and_station_are_fail_closed(self):
        with self.assertRaises(AccessError):
            self.station._authenticate_pin(
                "QRTEST01", "58420936", "1.1.1.1", remember=True
            )
        self.station.enabled = False
        with self.assertRaises(UserError):
            self.station._assert_network("8.8.8.8")
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.station.write({"enabled": True, "allowed_networks": False})

    def test_pin_rate_limit_persists_expected_failures(self):
        for _index in range(5):
            self._expect_access_error_without_rollback(
                lambda: self.station._authenticate_pin(
                    "QRTEST01", "00000000", "8.8.8.8", remember=True
                )
            )
        self._expect_access_error_without_rollback(self._login)

    def test_unknown_codes_use_bounded_rate_rows(self):
        with patch.object(attendance_model, "verify_pin", return_value=False):
            for index in range(35):
                self._expect_access_error_without_rollback(
                    lambda index=index: self.station._authenticate_pin(
                        f"BAD{index:04d}", "00000000", "8.8.8.8", remember=False
                    )
                )
        rates = self.env["ranvals.qr.rate"].sudo().search(
            [("station_id", "=", self.station.id)]
        )
        self.assertEqual(len(rates), 2, "Only the IP and shared unknown-code buckets may exist")

    def test_device_revoked_on_pin_policy_and_station_changes(self):
        _device, raw_token = self._login()
        self.assertTrue(self.station._device_from_token(raw_token))
        self.credential._set_pin("93642085")
        self.assertFalse(self.station._device_from_token(raw_token))

        self.credential._set_pin("58420936")
        _device, raw_token = self._login()
        self.station.remember_days = 31
        self.assertFalse(self.station._device_from_token(raw_token))

    def test_expired_and_idle_devices_are_deleted(self):
        device, raw_token = self._login()
        device.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        self.assertFalse(self.station._device_from_token(raw_token))
        self.assertFalse(device.exists())

        device, raw_token = self._login()
        device.last_seen_at = fields.Datetime.now() - timedelta(
            hours=self.station.remember_idle_hours + 1
        )
        self.assertFalse(self.station._device_from_token(raw_token))
        self.assertFalse(device.exists())

    def test_archiving_and_reactivating_employee_never_resurrects_token(self):
        _device, raw_token = self._login()
        self.employee.active = False
        self.employee.active = True
        self.assertFalse(self.station._device_from_token(raw_token))

    def test_device_limit_is_enforced_under_credential_serialization(self):
        self.station.max_devices_per_employee = 2
        _one, raw_one = self._login()
        _two, raw_two = self._login()
        _three, raw_three = self._login()
        self.assertFalse(self.station._device_from_token(raw_one))
        self.assertTrue(self.station._device_from_token(raw_two))
        self.assertTrue(self.station._device_from_token(raw_three))
        self.assertEqual(
            self.env["ranvals.qr.device"].sudo().search_count(
                [("credential_id", "=", self.credential.id)]
            ),
            2,
        )

    def test_in_out_and_idempotent_replay(self):
        device, _raw_token = self._login()
        with patch.object(fields.Datetime, "now", return_value=self.clock):
            intent = self._intent()
            first = self.station._perform(device, intent, "8.8.8.8")
            replay = self.station._perform(device, intent, "8.8.8.8")
            self.assertEqual(first, replay)
            self.assertFalse(first.attendance_id.check_out)
            self.assertEqual(first.attendance_id.ranvals_qr_in_station_id, self.station)

        with patch.object(
            fields.Datetime, "now", return_value=self.clock + timedelta(minutes=5)
        ):
            operation = self.station._perform(device, self._intent(), "8.8.8.8")
            self.assertEqual(operation.action, "out")
            self.assertEqual(operation.attendance_id, first.attendance_id)
            self.assertEqual(
                operation.attendance_id.check_out, self.clock + timedelta(minutes=5)
            )
            self.assertEqual(
                operation.attendance_id.ranvals_qr_out_station_id, self.station
            )

    def test_two_fresh_intents_cannot_toggle_twice(self):
        device, _raw_token = self._login()
        with patch.object(fields.Datetime, "now", return_value=self.clock):
            first_intent = self._intent()
            second_intent = self._intent()
            operation = self.station._perform(device, first_intent, "8.8.8.8")
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self.station._perform(device, second_intent, "8.8.8.8")
            self.assertFalse(operation.attendance_id.check_out)

    def test_cooldown_blocks_even_with_a_new_form(self):
        device, _raw_token = self._login()
        with patch.object(fields.Datetime, "now", return_value=self.clock):
            self.station._perform(device, self._intent(), "8.8.8.8")
            fresh_intent = self._intent()
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self.station._perform(device, fresh_intent, "8.8.8.8")

    def test_stale_and_future_entries_require_manager_review(self):
        stale = self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": fields.Datetime.now() - timedelta(hours=20),
            }
        )
        state = self.station._public_state(self.credential)
        self.assertTrue(state["review"])
        device, _raw_token = self._login()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.station._perform(device, state["intent"], "8.8.8.8")
        stale.unlink()

        future = self.env["hr.attendance"].create(
            {
                "employee_id": self.employee.id,
                "check_in": fields.Datetime.now() + timedelta(hours=1),
            }
        )
        state = self.station._public_state(self.credential)
        self.assertTrue(state["review"])
        future.unlink()

    def test_network_and_device_are_rechecked_at_submit(self):
        device, _raw_token = self._login()
        intent = self._intent()
        with self.assertRaises(AccessError):
            self.station._perform(device, intent, "1.1.1.1")
        device.unlink()
        with self.assertRaises(AccessError):
            self.station._perform(device, intent, "8.8.8.8")

    def test_intent_cannot_switch_person_or_station(self):
        other_employee = self.env["hr.employee"].create(
            {"name": "Other QR Employee", "company_id": self.env.company.id}
        )
        other_credential = self.env["ranvals.qr.credential"].create(
            {
                "employee_id": other_employee.id,
                "company_id": self.env.company.id,
                "code": "QRTEST02",
            }
        )
        other_credential._set_pin("93642085")
        other_device, _raw = self.station._authenticate_pin(
            "QRTEST02", "93642085", "8.8.8.8", remember=True
        )
        with self.assertRaises(AccessError):
            self.station._perform(other_device, self._intent(), "8.8.8.8")

        _device, raw_token = self._login()
        other_station = self.station.copy({"name": "Other Entrance"})
        self.assertFalse(other_station._device_from_token(raw_token))

    def test_qr_only_blocks_standard_channel_even_when_credential_inactive(self):
        with self.assertRaises(UserError):
            self.employee._attendance_action_change()
        self.credential.active = False
        with self.assertRaises(UserError):
            self.employee._attendance_action_change()

    def test_standard_channel_allowed_only_after_explicit_policy_change(self):
        self.credential.qr_only = False
        attendance = self.employee._attendance_action_change()
        self.assertEqual(attendance.employee_id, self.employee)
        self.assertFalse(attendance.ranvals_qr_in_station_id)

    def test_public_and_portal_users_cannot_manage_qr_models(self):
        public = self.env.ref("base.public_user")
        for model in (
            "ranvals.qr.station",
            "ranvals.qr.credential",
            "ranvals.qr.device",
            "ranvals.qr.rate",
            "ranvals.qr.operation",
        ):
            with self.assertRaises(AccessError):
                self.env[model].with_user(public).search_read([], ["id"])
        portal = new_test_user(
            self.env,
            login="ranvals_qr_portal_test",
            groups="base.group_portal",
            lang="en_US",
        )
        with self.assertRaises(AccessError):
            self.credential.with_user(portal).with_context(lang="en_US").action_rotate_pin()

    def test_secret_and_provenance_fields_cannot_be_forged(self):
        station = self.env["ranvals.qr.station"].with_user(self.manager).browse(
            self.station.id
        )
        self.assertTrue(station.kiosk_url, "compute_sudo must let a manager render the QR")
        with self.assertRaises(AccessError):
            station.read(["token"])
        with self.assertRaises(AccessError):
            self.env["hr.attendance"].with_user(self.manager).create(
                {
                    "employee_id": self.employee.id,
                    "ranvals_qr_in_station_id": self.station.id,
                }
            )

    def test_cross_company_links_are_rejected(self):
        other_company = self.env["res.company"].create({"name": "QR Other Company"})
        other_employee = self.env["hr.employee"].create(
            {"name": "Other Company Employee", "company_id": other_company.id}
        )
        other_credential = (
            self.env["ranvals.qr.credential"]
            .sudo()
            .with_company(other_company)
            .create(
                {
                    "employee_id": other_employee.id,
                    "company_id": other_company.id,
                    "code": "QRTEST03",
                }
            )
        )
        other_credential._set_pin("93642085")
        with self.assertRaises(AccessError):
            self.station._assert_credential(other_credential)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.env["ranvals.qr.device"].sudo().create(
                {
                    "station_id": self.station.id,
                    "credential_id": other_credential.id,
                    "token_digest": "a" * 64,
                    "credential_version": other_credential.version_key,
                    "last_seen_at": fields.Datetime.now(),
                    "expires_at": fields.Datetime.now() + timedelta(hours=1),
                }
            )

    def test_company_transfer_preserves_history_namespace_and_requires_new_identity(self):
        old_company = self.credential.company_id
        other_company = self.env["res.company"].create({"name": "QR Destination"})
        self.employee.company_id = other_company
        self.assertEqual(self.credential.company_id, old_company)
        self.assertFalse(self.credential.active)
        replacement = (
            self.env["ranvals.qr.credential"]
            .sudo()
            .with_company(other_company)
            .create(
                {
                    "employee_id": self.employee.id,
                    "company_id": other_company.id,
                    "code": "QRNEW01",
                }
            )
        )
        self.assertEqual(replacement.company_id, other_company)

    def test_cleanup_removes_expired_security_rows(self):
        device, _raw_token = self._login()
        device.expires_at = fields.Datetime.now() - timedelta(seconds=1)
        processed, remaining = self.env["ranvals.qr.device"]._cleanup_batch(limit=30)
        self.assertGreaterEqual(processed, 1)
        self.assertEqual(remaining, 0)
        self.assertFalse(device.exists())
