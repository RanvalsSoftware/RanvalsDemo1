import hmac
import json
import logging

from odoo import http
from odoo.http import request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.wrappers import Response

from ..tools import extract_leadgen_events, verify_hub_signature


_logger = logging.getLogger(__name__)
_MAX_WEBHOOK_BYTES = 1024 * 1024


def _response(body, status=200, content_type="text/plain; charset=utf-8"):
    return Response(body, status=status, content_type=content_type)


class MetaLeadAdsWebhookController(http.Controller):
    @http.route(
        "/meta_lead_ads/webhook",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def verify_webhook(self, **_kwargs):
        mode = request.httprequest.args.get("hub.mode", "")
        supplied_token = request.httprequest.args.get("hub.verify_token", "")
        challenge = request.httprequest.args.get("hub.challenge", "")
        if mode != "subscribe" or not supplied_token or len(supplied_token) > 512:
            return _response("Forbidden", status=403)

        connections = request.env["meta.lead.connection"].sudo().search(
            [("active", "=", True)]
        )
        matched = any(
            connection.verify_token
            and hmac.compare_digest(
                connection.verify_token.encode("utf-8"), supplied_token.encode("utf-8")
            )
            and connection._environment_allowed()
            for connection in connections
        )
        if not matched:
            return _response("Forbidden", status=403)
        return _response(challenge, status=200)

    @http.route(
        "/meta_lead_ads/webhook",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def receive_webhook(self, **_kwargs):
        content_length = request.httprequest.content_length
        if content_length is not None and content_length > _MAX_WEBHOOK_BYTES:
            return _response("Payload too large", status=413)
        # Werkzeug's bounded stream also covers chunked/missing-length bodies.
        request.httprequest.max_content_length = _MAX_WEBHOOK_BYTES
        try:
            # Odoo 17-19 intentionally exposes get_data but not `stream` on its
            # HTTPRequest proxy. Its stable wrapped Werkzeug request lets us cap
            # the read explicitly even on Werkzeug versions before 2.3.
            wrapped_request = getattr(
                request.httprequest, "_HTTPRequest__wrapped", None
            )
            cached_body = (
                getattr(wrapped_request, "_cached_data", None)
                if wrapped_request is not None
                else None
            )
            if cached_body is not None:
                # Odoo retries serialization failures by dispatching the same
                # HTTP request again. Keep the verified-size body available so
                # a retry does not read an already-consumed WSGI stream.
                raw_body = cached_body
            elif wrapped_request is not None:
                raw_body = wrapped_request.stream.read(_MAX_WEBHOOK_BYTES + 1)
            else:  # defensive fallback for a future Odoo HTTPRequest wrapper
                raw_body = request.httprequest.get_data(cache=True)
        except RequestEntityTooLarge:
            return _response("Payload too large", status=413)
        if len(raw_body) > _MAX_WEBHOOK_BYTES:
            return _response("Payload too large", status=413)
        if wrapped_request is not None and cached_body is None:
            wrapped_request._cached_data = raw_body
        signature = request.httprequest.headers.get("X-Hub-Signature-256", "")

        connections = request.env["meta.lead.connection"].sudo().search(
            [("active", "=", True), ("app_secret", "!=", False)]
        )
        valid_secrets = {
            secret
            for secret in set(connections.mapped("app_secret"))
            if verify_hub_signature(raw_body, signature, secret)
        }
        signed_connections = connections.filtered(
            lambda item: item.app_secret in valid_secrets
        )
        if not signed_connections:
            return _response("Invalid signature", status=403)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _response("Invalid JSON", status=400)
        if not isinstance(payload, dict) or payload.get("object") != "page":
            return _response("Ignored", status=200)

        events = extract_leadgen_events(payload)
        raw_payload = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        queued = 0
        duplicates = 0
        for event_data in events:
            page_id = str(event_data.get("page_id") or "")
            connection = signed_connections.filtered(
                lambda item: item.page_id == page_id
                and item.webhook_enabled
                and item._environment_allowed()
            )[:1]
            if not connection:
                continue
            _event, created = request.env["meta.lead.event"].sudo().create_from_webhook(
                connection, event_data, raw_payload=raw_payload
            )
            if created:
                queued += 1
            else:
                duplicates += 1

        _logger.info(
            "Meta Lead Ads webhook accepted queued=%s duplicate=%s", queued, duplicates
        )
        body = json.dumps(
            {"status": "ok", "queued": queued, "duplicates": duplicates},
            separators=(",", ":"),
        )
        return _response(body, status=200, content_type="application/json; charset=utf-8")
