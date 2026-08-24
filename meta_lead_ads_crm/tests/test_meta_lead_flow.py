import json
import os
import uuid
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.meta_connection import MetaGraphAPIError


@tagged("post_install", "-at_install")
class TestMetaLeadFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["meta.lead.connection"].create(
            {
                "name": "Test Page",
                "company_id": cls.env.company.id,
                "page_id": "987654321",
                "app_secret": "test-app-secret",
                "access_token": "test-page-token",
                "lead_type": "lead",
            }
        )

    def _payload(self, **overrides):
        payload = {
            "id": "TEST-" + uuid.uuid4().hex,
            "created_time": "2026-08-24T07:00:00+00:00",
            "campaign_id": "100",
            "campaign_name": "August Campaign",
            "adset_id": "200",
            "adset_name": "Istanbul",
            "ad_id": "300",
            "ad_name": "Video A",
            "form_id": "400",
            "form_name": "Quote Form",
            "platform": "instagram",
            "field_data": [
                {"name": "full_name", "values": ["Ada Lovelace"]},
                {"name": "email", "values": ["ADA@example.com"]},
                {"name": "phone_number", "values": ["+90 555 111 22 33"]},
                {"name": "company_name", "values": ["Analytical Engines"]},
                {"name": "interested_product", "values": ["CRM", "Sales"]},
            ],
        }
        payload.update(overrides)
        return payload

    def _event(self, payload=None):
        payload = payload or self._payload()
        return self.env["meta.lead.event"].create(
            {
                "connection_id": self.connection.id,
                "meta_leadgen_id": payload["id"],
                "page_id": self.connection.page_id,
                "form_id": payload.get("form_id"),
                "is_test": True,
                "lead_payload": json.dumps(payload),
            }
        )

    def test_end_to_end_mapping_and_attribution(self):
        event = self._event()
        lead = event._process_one()
        self.assertTrue(lead)
        self.assertEqual(event.state, "done")
        self.assertEqual(event.result, "created")
        self.assertEqual(lead.contact_name, "Ada Lovelace")
        self.assertEqual(lead.email_from, "ADA@example.com")
        self.assertEqual(lead.phone, "+90 555 111 22 33")
        self.assertEqual(lead.meta_campaign_id, "100")
        self.assertEqual(lead.meta_adset_id, "200")
        self.assertEqual(lead.meta_ad_id, "300")
        self.assertEqual(lead.meta_form_id, "400")
        self.assertEqual(lead.meta_platform, "instagram")
        self.assertIn("interested product", (lead.description or "").lower())
        self.assertIn(self.connection.tag_id, lead.tag_ids)

    def test_missing_contact_data_still_creates_lead(self):
        payload = self._payload(
            field_data=[{"name": "question", "values": ["Answer"]}]
        )
        event = self._event(payload)
        lead = event._process_one()
        self.assertTrue(lead)
        self.assertIn(event.meta_leadgen_id, lead.name)

    def test_same_webhook_is_atomically_idempotent(self):
        event_data = {
            "leadgen_id": "7654321",
            "page_id": self.connection.page_id,
            "form_id": "400",
        }
        first, created_first = self.env["meta.lead.event"].create_from_webhook(
            self.connection, event_data
        )
        second, created_second = self.env["meta.lead.event"].create_from_webhook(
            self.connection, event_data
        )
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first, second)

    def test_optional_email_dedup_links_without_overwrite(self):
        self.connection.write({"duplicate_policy": "email"})
        first = self._event()
        lead = first._process_one()
        lead.phone = "Do not overwrite"

        payload = self._payload(
            field_data=[
                {"name": "full_name", "values": ["Ada Again"]},
                {"name": "email", "values": [" ada@EXAMPLE.com "]},
                {"name": "phone_number", "values": ["+90 000 000 00 00"]},
            ]
        )
        second = self._event(payload)
        linked = second._process_one()
        self.assertEqual(linked, lead)
        self.assertEqual(second.result, "linked")
        self.assertEqual(lead.phone, "Do not overwrite")

    def test_custom_field_alias_and_routing(self):
        team = self.env["crm.team"].create(
            {"name": "Meta Team", "company_id": self.env.company.id}
        )
        self.env["meta.lead.routing.rule"].create(
            {
                "name": "Instagram",
                "connection_id": self.connection.id,
                "match_field": "platform",
                "match_value": "Instagram",
                "team_id": team.id,
            }
        )
        self.env["meta.lead.field.mapping"].create(
            {
                "connection_id": self.connection.id,
                "meta_field_name": "work_email",
                "target_field": "email_from",
            }
        )
        payload = self._payload(
            field_data=[
                {"name": "full_name", "values": ["Grace Hopper"]},
                {"name": "work_email", "values": ["grace@example.com"]},
            ]
        )
        lead = self._event(payload)._process_one()
        self.assertEqual(lead.email_from, "grace@example.com")
        self.assertEqual(lead.team_id, team)

    def test_custom_answer_html_is_escaped(self):
        payload = self._payload(
            field_data=[
                {"name": "full_name", "values": ["Safe Person"]},
                {"name": "custom", "values": ["<script>alert(1)</script>"]},
            ]
        )
        lead = self._event(payload)._process_one()
        self.assertNotIn("<script>", lead.description or "")

    def test_database_error_rolls_back_partial_work_and_schedules_retry(self):
        event = self._event()

        def abort_transaction(record, _payload):
            record.env.cr.execute("SELECT 1 / 0")

        with patch.object(type(event), "_create_or_link_crm_lead", abort_transaction):
            lead = event._process_one()

        event.invalidate_recordset()
        self.assertFalse(lead)
        self.assertEqual(event.state, "retry")
        self.assertEqual(event.attempts, 1)
        self.assertFalse(event.crm_lead_id)

    def test_real_processing_is_blocked_on_staging(self):
        event = self.env["meta.lead.event"].create(
            {
                "connection_id": self.connection.id,
                "meta_leadgen_id": "STAGING-" + uuid.uuid4().hex,
                "page_id": self.connection.page_id,
            }
        )
        with patch.dict(os.environ, {"ODOO_STAGE": "staging"}, clear=False):
            with self.assertRaises(UserError):
                event._process_one()
        event.invalidate_recordset()
        self.assertEqual(event.state, "pending")
        self.assertEqual(event.attempts, 0)

    def test_manual_retry_starts_a_fresh_retry_budget(self):
        event = self._event()
        event.write({"state": "failed", "attempts": self.connection.max_retries})
        event.action_retry()
        event.invalidate_recordset()
        self.assertEqual(event.state, "done")
        self.assertEqual(event.attempts, 1)

    def test_zero_day_retention_keeps_pending_but_purges_finished_payloads(self):
        self.connection.payload_retention_days = 0
        pending = self.env["meta.lead.event"].create(
            {
                "connection_id": self.connection.id,
                "meta_leadgen_id": "PENDING-" + uuid.uuid4().hex,
                "page_id": self.connection.page_id,
                "webhook_payload": '{"pending":true}',
            }
        )
        finished = self._event()
        finished.webhook_payload = '{"done":true}'
        finished._process_one()

        self.env["meta.lead.event"]._cron_purge_payloads()
        pending.invalidate_recordset()
        finished.invalidate_recordset()
        self.assertTrue(pending.webhook_payload)
        self.assertFalse(finished.webhook_payload)
        self.assertFalse(finished.lead_payload)

    def test_graph_request_rejects_dot_path_segments_before_network(self):
        with self.assertRaises(MetaGraphAPIError):
            self.connection._graph_request("GET", "../me")

    def test_failed_connection_check_persists_status_without_rpc_rollback(self):
        def fail_graph(_record, _method, _node, _params=None):
            raise MetaGraphAPIError("Sanitized test failure", retryable=False)

        with patch.object(type(self.connection), "_graph_request", fail_graph):
            action = self.connection.action_test_connection()

        self.assertEqual(self.connection.state, "error")
        self.assertEqual(self.connection.last_error, "Sanitized test failure")
        self.assertEqual(action["params"]["type"], "danger")
