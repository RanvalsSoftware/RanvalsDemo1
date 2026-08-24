from unittest.mock import Mock, patch

import requests

from odoo.tests import TransactionCase, tagged

from ..models import meta_connection
from ..models.meta_connection import MetaGraphAPIError


@tagged("post_install", "-at_install")
class TestMetaGraphAPI(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connection = cls.env["meta.lead.connection"].create(
            {
                "name": "Graph API Test Page",
                "company_id": cls.env.company.id,
                "page_id": "998877665544",
                "app_secret": "graph-test-app-secret",
                "access_token": "graph-test-page-access-token",
                "verify_token": "graph-test-webhook-verify-token",
            }
        )

    @staticmethod
    def _response(status_code, payload):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        return response

    def test_rate_limit_and_server_errors_are_retryable(self):
        for status_code in (429, 500, 503):
            with self.subTest(status_code=status_code):
                response = self._response(
                    status_code,
                    {
                        "error": {
                            "message": "Temporary Meta failure",
                            "code": 4,
                            "is_transient": False,
                        }
                    },
                )
                with patch.object(
                    meta_connection.requests, "request", return_value=response
                ):
                    with self.assertRaises(MetaGraphAPIError) as raised:
                        self.connection._graph_request("GET", "123456")

                self.assertTrue(raised.exception.retryable)
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertEqual(raised.exception.error_code, 4)

    def test_permanent_client_error_is_not_retryable(self):
        response = self._response(
            403,
            {
                "error": {
                    "message": "Invalid OAuth access token",
                    "code": 190,
                    "is_transient": False,
                }
            },
        )
        with patch.object(meta_connection.requests, "request", return_value=response):
            with self.assertRaises(MetaGraphAPIError) as raised:
                self.connection._graph_request("GET", "123456")

        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.error_code, 190)

    def test_permission_errors_retry_with_minimum_lead_fields(self):
        lead_payload = {
            "id": "123456",
            "created_time": "2026-08-24T07:00:00+00:00",
            "field_data": [{"name": "email", "values": ["lead@example.com"]}],
        }
        for error_code in (10, 100, 200):
            with self.subTest(error_code=error_code):
                permission_error = self._response(
                    400,
                    {
                        "error": {
                            "message": "Optional advertising field is unavailable",
                            "code": error_code,
                            "is_transient": False,
                        }
                    },
                )
                success = self._response(200, lead_payload)
                with patch.object(
                    meta_connection.requests,
                    "request",
                    side_effect=[permission_error, success],
                ) as request_mock:
                    result = self.connection._fetch_lead_details("123456")

                self.assertEqual(result, lead_payload)
                self.assertEqual(request_mock.call_count, 2)
                first_fields = request_mock.call_args_list[0].kwargs["params"]["fields"]
                fallback_fields = request_mock.call_args_list[1].kwargs["params"]["fields"]
                self.assertIn("campaign_name", first_fields)
                self.assertEqual(
                    fallback_fields,
                    "id,created_time,ad_id,form_id,field_data",
                )

    def test_timeout_is_retryable_and_does_not_leak_credentials(self):
        leaked_message = " ".join(
            (
                "Timed out while using",
                self.connection.access_token,
                self.connection.app_secret,
                self.connection.verify_token,
            )
        )
        with patch.object(
            meta_connection.requests,
            "request",
            side_effect=requests.Timeout(leaked_message),
        ) as request_mock:
            with self.assertRaises(MetaGraphAPIError) as raised:
                self.connection._graph_request("GET", "123456")

        rendered_error = str(raised.exception)
        self.assertTrue(raised.exception.retryable)
        self.assertIn("[REDACTED]", rendered_error)
        for secret in (
            self.connection.access_token,
            self.connection.app_secret,
            self.connection.verify_token,
        ):
            self.assertNotIn(secret, rendered_error)
        self.assertEqual(request_mock.call_args.kwargs["timeout"], (5, 20))

    def test_graph_error_message_redacts_all_configured_secrets(self):
        leaked_message = "token=%s secret=%s verify=%s" % (
            self.connection.access_token,
            self.connection.app_secret,
            self.connection.verify_token,
        )
        response = self._response(
            400,
            {
                "error": {
                    "message": leaked_message,
                    "code": 190,
                    "is_transient": False,
                }
            },
        )
        with patch.object(meta_connection.requests, "request", return_value=response):
            with self.assertRaises(MetaGraphAPIError) as raised:
                self.connection._graph_request("GET", "123456")

        rendered_error = str(raised.exception)
        self.assertIn("[REDACTED]", rendered_error)
        for secret in (
            self.connection.access_token,
            self.connection.app_secret,
            self.connection.verify_token,
        ):
            self.assertNotIn(secret, rendered_error)

