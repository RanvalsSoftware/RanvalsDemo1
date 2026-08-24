import time
import uuid
from queue import Queue
from threading import Thread

from odoo import SUPERUSER_ID, api
from odoo.modules.registry import Registry
from odoo.tests import tagged
from odoo.tests.common import BaseCase, get_db_name


@tagged("post_install", "-at_install")
class TestMetaLeadDatabaseConcurrency(BaseCase):
    """Exercise PostgreSQL locking with real, independently committed cursors."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = Registry(get_db_name())

    def test_concurrent_webhook_delivery_is_atomically_idempotent(self):
        unique_suffix = uuid.uuid4().hex
        page_id = str(uuid.uuid4().int)
        leadgen_id = "CONCURRENT-" + unique_suffix
        connection_id = None
        holder = None
        holder_ready_queue = Queue(maxsize=1)
        second_pid_queue = Queue(maxsize=1)
        holder_result_queue = Queue(maxsize=1)

        try:
            # Commit shared setup before either competing transaction starts.
            with self.registry.cursor() as setup_cr:
                setup_env = api.Environment(setup_cr, SUPERUSER_ID, {})
                company_id = setup_env.company.id
                connection = setup_env["meta.lead.connection"].create(
                    {
                        "name": "Concurrency test " + unique_suffix,
                        "company_id": company_id,
                        "page_id": page_id,
                    }
                )
                connection_id = connection.id

            # Odoo 19 holds its registry lock while post-install tests run, so
            # the background transaction intentionally uses SQL only. The main
            # thread below exercises the real create_from_webhook method.
            def hold_first_delivery_uncommitted():
                ready_sent = False
                try:
                    with self.registry.cursor() as holder_cr:
                        holder_cr.execute("SET LOCAL statement_timeout TO '15s'")
                        holder_cr.execute("SELECT pg_backend_pid()")
                        holder_backend_pid = holder_cr.fetchone()[0]
                        holder_cr.execute(
                            """
                                INSERT INTO meta_lead_event (
                                    connection_id, company_id,
                                    meta_leadgen_id, page_id, form_id,
                                    state, attempts, is_test,
                                    create_uid, write_uid,
                                    create_date, write_date
                                )
                                VALUES (
                                    %s, %s, %s, %s, %s,
                                    'pending', 0, FALSE,
                                    %s, %s,
                                    NOW() AT TIME ZONE 'UTC',
                                    NOW() AT TIME ZONE 'UTC'
                                )
                                RETURNING id
                            """,
                            (
                                connection_id,
                                company_id,
                                leadgen_id,
                                page_id,
                                "CONCURRENCY-FORM",
                                SUPERUSER_ID,
                                SUPERUSER_ID,
                            ),
                        )
                        first_event_id = holder_cr.fetchone()[0]
                        holder_ready_queue.put(
                            ("ready", first_event_id, holder_backend_pid)
                        )
                        ready_sent = True

                        second_backend_pid = second_pid_queue.get(timeout=5)
                        deadline = time.monotonic() + 5.0
                        wait_event = None
                        while time.monotonic() < deadline:
                            # PostgreSQL may cache statistics reads until the
                            # transaction ends; force each poll to see the
                            # other backend's current wait state.
                            holder_cr.execute("SELECT pg_stat_clear_snapshot()")
                            holder_cr.execute(
                                """
                                    SELECT wait_event_type, wait_event
                                      FROM pg_stat_activity
                                     WHERE pid = %s
                                """,
                                (second_backend_pid,),
                            )
                            activity = holder_cr.fetchone()
                            if activity and activity[0] == "Lock":
                                wait_event = activity[1]
                                break
                            time.sleep(0.01)

                        # Always release the main call, even if observation
                        # itself regresses, so the test cannot deadlock.
                        holder_cr.commit()
                    holder_result_queue.put(
                        (
                            "ok",
                            first_event_id,
                            holder_backend_pid,
                            wait_event,
                        )
                    )
                except BaseException as exc:  # surfaced in the test thread
                    error = ("error", repr(exc))
                    if not ready_sent:
                        holder_ready_queue.put(error)
                    holder_result_queue.put(error)

            holder = Thread(
                target=hold_first_delivery_uncommitted,
                name="meta-lead-idempotency-holder",
                daemon=True,
            )
            holder.start()
            holder_ready = holder_ready_queue.get(timeout=5)
            self.assertEqual(holder_ready[0], "ready", holder_ready)
            _, first_event_id, first_backend_pid = holder_ready

            event_data = {
                "leadgen_id": leadgen_id,
                "page_id": page_id,
                "form_id": "CONCURRENCY-FORM",
            }
            with self.registry.cursor() as second_cr:
                second_env = api.Environment(second_cr, SUPERUSER_ID, {})
                second_cr.execute("SELECT pg_backend_pid()")
                second_backend_pid = second_cr.fetchone()[0]
                self.assertNotEqual(first_backend_pid, second_backend_pid)
                second_pid_queue.put(second_backend_pid)

                second_event, second_created = second_env[
                    "meta.lead.event"
                ].create_from_webhook(
                    second_env["meta.lead.connection"].browse(connection_id),
                    event_data,
                )
                self.assertFalse(second_created)
                self.assertEqual(second_event.id, first_event_id)

                # The SerializationFailure must have been rolled back to the
                # method's savepoint, not left the caller's transaction aborted.
                second_cr.execute("SELECT 1")
                self.assertEqual(second_cr.fetchone(), (1,))

            holder.join(timeout=10)
            self.assertFalse(holder.is_alive(), "The holder transaction did not finish")
            holder_result = holder_result_queue.get(timeout=1)
            self.assertEqual(holder_result[0], "ok", holder_result)
            _, held_event_id, held_backend_pid, wait_event = holder_result
            self.assertEqual(held_event_id, first_event_id)
            self.assertEqual(held_backend_pid, first_backend_pid)
            self.assertTrue(
                wait_event,
                "The second PostgreSQL transaction never waited on the "
                "uncommitted Meta Lead ID.",
            )

            with self.registry.cursor() as verify_cr:
                verify_env = api.Environment(verify_cr, SUPERUSER_ID, {})
                events = verify_env["meta.lead.event"].search(
                    [("meta_leadgen_id", "=", leadgen_id)]
                )
                self.assertEqual(len(events), 1)
                self.assertEqual(events.id, first_event_id)
        finally:
            # All queue waits and SQL statements are bounded; this join is only
            # a final guard before deleting the committed fixture records.
            if holder is not None and holder.is_alive():
                holder.join(timeout=16)

            if connection_id is not None:
                with self.registry.cursor() as cleanup_cr:
                    cleanup_env = api.Environment(cleanup_cr, SUPERUSER_ID, {})
                    cleanup_env["meta.lead.event"].search(
                        [("meta_leadgen_id", "=", leadgen_id)]
                    ).unlink()
                    cleanup_env["meta.lead.connection"].browse(
                        connection_id
                    ).exists().unlink()
