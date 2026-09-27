"""Real PostgreSQL concurrency checks using independent Odoo cursors."""

import threading
from concurrent.futures import ThreadPoolExecutor

from psycopg2 import errors

from odoo import api
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tests import BaseCase, tagged
from odoo.tests.common import get_db_name
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "ranvals_qr")
class TestRanvalsQRConcurrency(BaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            station = env["ranvals.qr.station"].create(
                {
                    "name": "CONCURRENCY TEST — NOT LIVE",
                    "base_url": "https://concurrency.example.test",
                    "allowed_networks": "8.8.8.8/32",
                    "enabled": True,
                    "cooldown_seconds": 10,
                }
            )
            employee = env["hr.employee"].create(
                {"name": "QR Concurrency Employee", "company_id": env.company.id}
            )
            credential = env["ranvals.qr.credential"].create(
                {
                    "employee_id": employee.id,
                    "company_id": env.company.id,
                    "code": "QRCONCUR1",
                }
            )
            credential._set_pin("58420936")
            device_one, _raw_one = station._authenticate_pin(
                "QRCONCUR1", "58420936", "8.8.8.8", remember=True
            )
            device_two, _raw_two = station._authenticate_pin(
                "QRCONCUR1", "58420936", "8.8.8.8", remember=True
            )
            cls.station_id = station.id
            cls.employee_id = employee.id
            cls.credential_id = credential.id
            cls.device_ids = (device_one.id, device_two.id)
            cls.intents = (
                station._public_state(credential)["intent"],
                station._public_state(credential)["intent"],
            )
        cls.addClassCleanup(cls._cleanup_records)

    @classmethod
    def _cleanup_records(cls):
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            env["ranvals.qr.operation"].search(
                [("credential_id", "=", cls.credential_id)]
            ).unlink()
            env["hr.attendance"].search(
                [("employee_id", "=", cls.employee_id)]
            ).unlink()
            env["ranvals.qr.device"].search(
                [("credential_id", "=", cls.credential_id)]
            ).unlink()
            env["ranvals.qr.credential"].browse(cls.credential_id).unlink()
            env["ranvals.qr.station"].browse(cls.station_id).unlink()
            env["hr.employee"].browse(cls.employee_id).unlink()

    @mute_logger("odoo.sql_db")
    def test_simultaneous_signed_transitions_create_exactly_one_attendance(self):
        barrier = threading.Barrier(2)

        def run(env, device_id, intent):
            try:
                station = env["ranvals.qr.station"].browse(self.station_id)
                device = env["ranvals.qr.device"].browse(device_id)
                # Establish both REPEATABLE READ snapshots before either
                # transaction advances to the serialization row.
                station.read(["enabled"])
                device.read(["expires_at"])
                barrier.wait(timeout=5)
                operation = station._perform(device, intent, "8.8.8.8")
                env.cr.commit()
                return f"ok:{operation.id}"
            except UserError:
                env.cr.rollback()
                return "conflict"
            except errors.SerializationFailure:
                env.cr.rollback()
                return "serialization"

        # Construct environments on the main test thread while Odoo's registry
        # loader owns its re-entrant lock, then dedicate one to each worker.
        cursors = [self.registry.cursor(), self.registry.cursor()]
        environments = [
            api.Environment(cr, api.SUPERUSER_ID, {}) for cr in cursors
        ]
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(run, env, device_id, intent)
                    for env, device_id, intent in zip(
                        environments, self.device_ids, self.intents
                    )
                ]
                results = [future.result(timeout=20) for future in futures]
        finally:
            for cr in cursors:
                cr.close()

        self.assertEqual(sum(result.startswith("ok:") for result in results), 1)
        self.assertEqual(sum(result in ("serialization", "conflict") for result in results), 1)
        losing_index = next(
            index for index, result in enumerate(results) if not result.startswith("ok:")
        )

        with self.registry.cursor() as cr:
            env = api.Environment(cr, api.SUPERUSER_ID, {})
            self.assertEqual(
                env["hr.attendance"].search_count(
                    [("employee_id", "=", self.employee_id)]
                ),
                1,
            )
            self.assertEqual(
                env["ranvals.qr.operation"].search_count(
                    [("credential_id", "=", self.credential_id)]
                ),
                1,
            )
            station = env["ranvals.qr.station"].browse(self.station_id)
            device = env["ranvals.qr.device"].browse(self.device_ids[losing_index])
            with self.assertRaises(UserError), cr.savepoint():
                station._perform(device, self.intents[losing_index], "8.8.8.8")
