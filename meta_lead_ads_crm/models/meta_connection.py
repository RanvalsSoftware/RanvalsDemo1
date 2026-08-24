import hashlib
import hmac
import logging
import os
import re
import secrets
from urllib.parse import quote

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import redact_secret, validate_graph_version


_logger = logging.getLogger(__name__)


class MetaGraphAPIError(Exception):
    """A sanitized Graph API failure suitable for the retry queue."""

    def __init__(self, message, *, status_code=0, error_code=0, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = retryable


class MetaLeadConnection(models.Model):
    _name = "meta.lead.connection"
    _description = "Meta Lead Ads Connection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, name, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    page_id = fields.Char(
        string="Facebook Page ID", required=True, copy=False, index=True, tracking=True
    )
    page_name = fields.Char(readonly=True, copy=False, tracking=True)
    app_id = fields.Char(string="Meta App ID", copy=False, groups="base.group_system")
    app_secret = fields.Char(
        string="Meta App Secret", copy=False, groups="base.group_system"
    )
    access_token = fields.Char(
        string="Page Access Token", copy=False, groups="base.group_system"
    )
    verify_token = fields.Char(
        string="Webhook Verify Token",
        required=True,
        copy=False,
        default=lambda self: secrets.token_urlsafe(32),
        groups="base.group_system",
    )
    graph_api_version = fields.Char(
        required=True,
        default="v26.0",
        help="Meta Graph API version, for example v26.0.",
    )
    webhook_url = fields.Char(compute="_compute_webhook_url")
    webhook_enabled = fields.Boolean(
        string="Accept Webhooks",
        default=False,
        tracking=True,
        help="Enable only after the credentials and webhook subscription are ready.",
    )
    allow_non_production = fields.Boolean(
        string="Allow on Odoo.sh Staging/Development",
        default=False,
        help=(
            "Odoo.sh database copies are blocked by default. Enable this only while "
            "testing a dedicated non-production Meta app/page."
        ),
    )

    lead_type = fields.Selection(
        [("lead", "Lead"), ("opportunity", "Opportunity")],
        required=True,
        default="lead",
        tracking=True,
    )
    team_id = fields.Many2one(
        "crm.team",
        string="Default Sales Team",
        check_company=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        tracking=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Default Salesperson",
        domain="[('share', '=', False), ('active', '=', True)]",
        tracking=True,
    )
    tag_id = fields.Many2one(
        "crm.tag",
        string="CRM Tag",
        default=lambda self: self.env.ref(
            "meta_lead_ads_crm.crm_tag_meta_lead_ads", raise_if_not_found=False
        ),
    )

    duplicate_policy = fields.Selection(
        [
            ("meta_only", "Only same Meta Lead ID"),
            ("email", "Link by email"),
            ("phone", "Link by phone"),
            ("email_or_phone", "Link by email or phone"),
        ],
        required=True,
        default="meta_only",
        tracking=True,
        help=(
            "Meta Lead ID is always idempotent. Optional contact matching links a new "
            "submission to the newest open CRM record instead of silently overwriting it."
        ),
    )
    update_missing_on_duplicate = fields.Boolean(
        string="Fill Empty Contact Fields on Match",
        default=True,
        help="Only empty CRM contact fields are filled; existing values are never replaced.",
    )

    store_payloads = fields.Boolean(
        string="Store Raw Payloads",
        default=False,
        help="Off by default to minimize stored personal data. IDs and processing status remain available.",
    )
    payload_retention_days = fields.Integer(
        default=30,
        help="Raw webhook/Graph payloads older than this are erased. Set to 0 to erase after processing.",
    )
    max_retries = fields.Integer(default=8, required=True)
    processing_batch_size = fields.Integer(default=20, required=True)

    state = fields.Selection(
        [("draft", "Not Tested"), ("connected", "Connected"), ("error", "Error")],
        default="draft",
        readonly=True,
        copy=False,
        tracking=True,
    )
    last_test_at = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False, groups="base.group_system")

    routing_rule_ids = fields.One2many(
        "meta.lead.routing.rule", "connection_id", string="Routing Rules"
    )
    field_mapping_ids = fields.One2many(
        "meta.lead.field.mapping", "connection_id", string="Field Mappings"
    )
    event_ids = fields.One2many("meta.lead.event", "connection_id", string="Events")
    event_count = fields.Integer(compute="_compute_event_count")

    if hasattr(models, "Constraint"):
        _page_id_unique = models.Constraint(
            "UNIQUE(page_id)", "A Facebook Page ID can only have one active connection record."
        )
    else:
        _sql_constraints = [
            (
                "page_id_unique",
                "unique(page_id)",
                "A Facebook Page ID can only have one active connection record.",
            )
        ]

    @api.depends("page_id")
    def _compute_webhook_url(self):
        base_url = (
            self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        ).rstrip("/")
        callback = f"{base_url}/meta_lead_ads/webhook" if base_url else "/meta_lead_ads/webhook"
        for connection in self:
            connection.webhook_url = callback

    def _compute_event_count(self):
        grouped = self.env["meta.lead.event"].read_group(
            [("connection_id", "in", self.ids)], ["connection_id"], ["connection_id"]
        )
        counts = {item["connection_id"][0]: item["connection_id_count"] for item in grouped}
        for connection in self:
            connection.event_count = counts.get(connection.id, 0)

    @api.constrains("page_id")
    def _check_page_id(self):
        for connection in self:
            if not re.fullmatch(r"[0-9]+", connection.page_id or ""):
                raise ValidationError(_("Facebook Page ID must contain digits only."))

    @api.constrains("graph_api_version")
    def _check_graph_api_version(self):
        for connection in self:
            if not validate_graph_version(connection.graph_api_version):
                raise ValidationError(_("Graph API version must look like v26.0."))

    @api.constrains("webhook_enabled", "app_secret", "access_token", "verify_token")
    def _check_webhook_credentials(self):
        for connection in self:
            if connection.webhook_enabled and not (
                connection.app_secret and connection.access_token and connection.verify_token
            ):
                raise ValidationError(
                    _(
                        "App Secret, Page Access Token, and Verify Token are required "
                        "before webhooks can be enabled."
                    )
                )

    @api.constrains("max_retries", "processing_batch_size", "payload_retention_days")
    def _check_limits(self):
        for connection in self:
            if not 1 <= connection.max_retries <= 25:
                raise ValidationError(_("Maximum retries must be between 1 and 25."))
            if not 1 <= connection.processing_batch_size <= 500:
                raise ValidationError(_("Batch size must be between 1 and 500."))
            if not 0 <= connection.payload_retention_days <= 3650:
                raise ValidationError(_("Payload retention must be between 0 and 3650 days."))

    @api.constrains("team_id", "user_id", "company_id")
    def _check_assignment_company(self):
        for connection in self:
            if connection.team_id.company_id and connection.team_id.company_id != connection.company_id:
                raise ValidationError(_("The default sales team belongs to another company."))
            if connection.user_id and not connection.user_id.active:
                raise ValidationError(_("The default salesperson must be active."))
            if connection.user_id and connection.company_id not in connection.user_id.company_ids:
                raise ValidationError(_("The default salesperson has no access to this company."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("verify_token", secrets.token_urlsafe(32))
        return super().create(vals_list)

    def write(self, vals):
        credentials = {"page_id", "app_id", "app_secret", "access_token", "graph_api_version"}
        if credentials.intersection(vals):
            vals = dict(vals, state="draft", last_error=False)
        return super().write(vals)

    def _environment_allowed(self):
        self.ensure_one()
        stage = (os.environ.get("ODOO_STAGE") or "").strip().lower()
        if not stage or stage == "production":
            return True
        return bool(self.allow_non_production)

    def _appsecret_proof(self):
        self.ensure_one()
        return hmac.new(
            (self.app_secret or "").encode("utf-8"),
            (self.access_token or "").encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _sanitized_error(self, value):
        self.ensure_one()
        return redact_secret(value, self.access_token, self.app_secret, self.verify_token)

    def _graph_request(self, method, node, params=None):
        self.ensure_one()
        connection = self.sudo()
        if not connection.access_token or not connection.app_secret:
            raise MetaGraphAPIError(_("Meta credentials are incomplete."), retryable=False)
        if not validate_graph_version(connection.graph_api_version):
            raise MetaGraphAPIError(_("Invalid Graph API version."), retryable=False)
        segments = str(node).strip("/").split("/")
        if not segments or any(
            part in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            for part in segments
        ):
            raise MetaGraphAPIError(_("Invalid Graph API object path."), retryable=False)

        encoded_node = "/".join(quote(part, safe="") for part in segments)
        url = f"https://graph.facebook.com/{connection.graph_api_version}/{encoded_node}"
        request_params = dict(params or {})
        request_params["appsecret_proof"] = connection._appsecret_proof()
        headers = {
            "Authorization": f"Bearer {connection.access_token}",
            "Accept": "application/json",
            "User-Agent": "Odoo-Meta-Lead-Ads-CRM/1.0",
        }
        try:
            response = requests.request(
                method.upper(),
                url,
                headers=headers,
                params=request_params,
                timeout=(5, 20),
            )
        except requests.RequestException as exc:
            message = connection._sanitized_error(str(exc))
            raise MetaGraphAPIError(
                _("Could not reach Meta Graph API: %s") % message,
                retryable=True,
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MetaGraphAPIError(
                _("Meta Graph API returned an invalid response."),
                status_code=response.status_code,
                retryable=response.status_code >= 500,
            ) from exc

        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if response.status_code >= 400 or error:
            message = connection._sanitized_error(
                error.get("message") if isinstance(error, dict) else _("Unknown Meta error")
            )
            code = int(error.get("code") or 0) if isinstance(error, dict) else 0
            is_transient = bool(error.get("is_transient")) if isinstance(error, dict) else False
            retryable = is_transient or response.status_code in (408, 425, 429) or response.status_code >= 500
            raise MetaGraphAPIError(
                _("Meta Graph API error (%(code)s): %(message)s")
                % {"code": code or response.status_code, "message": message},
                status_code=response.status_code,
                error_code=code,
                retryable=retryable,
            )
        if not isinstance(payload, dict):
            raise MetaGraphAPIError(_("Meta Graph API returned an unexpected response."))
        return payload

    def _fetch_lead_details(self, leadgen_id):
        fields_list = [
            "id",
            "created_time",
            "ad_id",
            "ad_name",
            "adset_id",
            "adset_name",
            "campaign_id",
            "campaign_name",
            "form_id",
            "is_organic",
            "platform",
            "field_data",
        ]
        try:
            return self._graph_request(
                "GET", str(leadgen_id), {"fields": ",".join(fields_list)}
            )
        except MetaGraphAPIError as exc:
            # Some page tokens can retrieve the lead but cannot read advertising
            # enrichment fields. Never lose the contact for optional attribution.
            if exc.retryable or exc.error_code not in (10, 100, 200):
                raise
            minimum_fields = "id,created_time,ad_id,form_id,field_data"
            return self._graph_request(
                "GET", str(leadgen_id), {"fields": minimum_fields}
            )

    def _fetch_form_name(self, form_id):
        if not form_id:
            return False
        try:
            return self._graph_request("GET", str(form_id), {"fields": "id,name"}).get("name")
        except MetaGraphAPIError as exc:
            _logger.info("Meta form name could not be fetched: %s", self._sanitized_error(exc))
            return False

    def action_test_connection(self):
        self.ensure_one()
        try:
            payload = self._graph_request("GET", self.page_id, {"fields": "id,name"})
        except MetaGraphAPIError as exc:
            message = self._sanitized_error(exc)
            self.write(
                {"state": "error", "last_test_at": fields.Datetime.now(), "last_error": message}
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Connection failed"),
                    "message": message,
                    "type": "danger",
                    "sticky": True,
                },
            }
        self.write(
            {
                "page_name": payload.get("name") or self.page_name,
                "state": "connected",
                "last_test_at": fields.Datetime.now(),
                "last_error": False,
            }
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection successful"),
                "message": _("Meta page %(name)s (%(id)s) is reachable.")
                % {"name": payload.get("name") or "", "id": payload.get("id") or self.page_id},
                "type": "success",
                "sticky": False,
            },
        }

    def action_subscribe_page(self):
        self.ensure_one()
        try:
            payload = self._graph_request(
                "POST", f"{self.page_id}/subscribed_apps", {"subscribed_fields": "leadgen"}
            )
        except MetaGraphAPIError as exc:
            message = self._sanitized_error(exc)
            self.write({"state": "error", "last_error": message})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Subscription failed"),
                    "message": message,
                    "type": "danger",
                    "sticky": True,
                },
            }
        if not payload.get("success"):
            message = _("Meta did not confirm the page subscription.")
            self.write({"state": "error", "last_error": message})
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Subscription failed"),
                    "message": message,
                    "type": "danger",
                    "sticky": True,
                },
            }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Page subscribed"),
                "message": _("The app is subscribed to the page's leadgen events."),
                "type": "success",
                "sticky": False,
            },
        }

    def action_regenerate_verify_token(self):
        self.ensure_one()
        self.verify_token = secrets.token_urlsafe(32)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Verify token regenerated"),
                "message": _("Update the webhook subscription in Meta before receiving new events."),
                "type": "warning",
                "sticky": True,
            },
        }

    def action_open_events(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "meta_lead_ads_crm.action_meta_lead_event"
        )
        action["domain"] = [("connection_id", "=", self.id)]
        action["context"] = {"default_connection_id": self.id}
        return action

    def action_open_test_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Test Meta Lead"),
            "res_model": "meta.lead.test.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_connection_id": self.id},
        }

    def action_process_pending(self):
        self.ensure_one()
        if not self._environment_allowed():
            raise UserError(
                _(
                    "Real Meta lead processing is blocked on this Odoo.sh staging/development "
                    "database. Enable the non-production option only for a dedicated test page."
                )
            )
        self.env["meta.lead.event"].sudo()._process_queue(connection=self)
        return self.action_open_events()
