import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from markupsafe import Markup, escape
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from psycopg2 import errors as pg_errors

from ..tools import (
    field_data_to_dict,
    first_field_value,
    normalize_email,
    normalize_phone,
)
from .meta_connection import MetaGraphAPIError


_logger = logging.getLogger(__name__)
_QUEUE_TIME_BUDGET_SECONDS = 240
_QUEUE_SLICE_SIZE = 10


STANDARD_FIELD_NAMES = {
    "full_name",
    "first_name",
    "last_name",
    "email",
    "email_address",
    "phone",
    "phone_number",
    "mobile",
    "company_name",
    "company",
    "job_title",
    "street_address",
    "street",
    "city",
    "zip_code",
    "post_code",
    "postal_code",
    "state",
    "province",
    "country",
}


class MetaLeadEvent(models.Model):
    _name = "meta.lead.event"
    _description = "Meta Lead Ads Event"
    _order = "create_date desc, id desc"
    _rec_name = "meta_leadgen_id"

    connection_id = fields.Many2one(
        "meta.lead.connection", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="connection_id.company_id", store=True, index=True, readonly=True
    )
    meta_leadgen_id = fields.Char(string="Meta Lead ID", required=True, copy=False, index=True)
    page_id = fields.Char(required=True, copy=False, index=True)
    form_id = fields.Char(copy=False, index=True)
    form_name = fields.Char(copy=False)
    campaign_id = fields.Char(copy=False, index=True)
    campaign_name = fields.Char(copy=False)
    adset_id = fields.Char(copy=False, index=True)
    adset_name = fields.Char(copy=False)
    ad_id = fields.Char(copy=False, index=True)
    ad_name = fields.Char(copy=False)
    platform = fields.Char(copy=False, index=True)
    meta_created_time = fields.Datetime(string="Submitted on Meta", copy=False)
    is_test = fields.Boolean(default=False, copy=False)

    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processing", "Processing"),
            ("retry", "Retry Scheduled"),
            ("done", "Done"),
            ("failed", "Failed"),
            ("ignored", "Ignored"),
        ],
        default="pending",
        required=True,
        copy=False,
        index=True,
    )
    result = fields.Selection(
        [("created", "CRM Lead Created"), ("linked", "Linked to Existing CRM Lead")],
        copy=False,
        readonly=True,
    )
    attempts = fields.Integer(default=0, copy=False, readonly=True)
    next_retry_at = fields.Datetime(copy=False, index=True, readonly=True)
    last_attempt_at = fields.Datetime(copy=False, readonly=True)
    processed_at = fields.Datetime(copy=False, readonly=True)
    last_error = fields.Text(copy=False, readonly=True, groups="base.group_system")
    crm_lead_id = fields.Many2one("crm.lead", copy=False, readonly=True, ondelete="set null")

    webhook_payload = fields.Text(copy=False, readonly=True, groups="base.group_system")
    lead_payload = fields.Text(copy=False, readonly=True, groups="base.group_system")

    if hasattr(models, "Constraint"):
        _leadgen_id_unique = models.Constraint(
            "UNIQUE(meta_leadgen_id)", "This Meta Lead ID has already been received."
        )
    else:
        _sql_constraints = [
            (
                "meta_leadgen_id_unique",
                "unique(meta_leadgen_id)",
                "This Meta Lead ID has already been received.",
            )
        ]

    @api.model
    def _parse_meta_datetime(self, value):
        if not value:
            return False
        if isinstance(value, (int, float)) or str(value).isdigit():
            try:
                return datetime.fromtimestamp(float(value), timezone.utc).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                return False
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed

    @api.model
    def create_from_webhook(self, connection, event_data, raw_payload=None):
        """Atomically enqueue one event and return ``(event, created)``.

        A concurrently committed duplicate may be an ID-only recordset in the
        caller's older REPEATABLE READ snapshot; webhook callers only need its
        identity and the ``created`` flag.
        """
        leadgen_id = str(event_data.get("leadgen_id") or "").strip()
        if not leadgen_id:
            return self.browse(), False
        now = fields.Datetime.now()
        # ON CONFLICT provides race-safe idempotency without producing noisy
        # database error logs for Meta's normal webhook redeliveries.
        try:
            # Odoo uses REPEATABLE READ. If another transaction commits the
            # same unique Meta ID while this INSERT waits, PostgreSQL can raise
            # SerializationFailure instead of taking the DO NOTHING branch.
            # The savepoint keeps the surrounding webhook transaction usable.
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    """
                        INSERT INTO meta_lead_event (
                            connection_id, company_id, meta_leadgen_id, page_id,
                            form_id, ad_id, meta_created_time, webhook_payload,
                            state, attempts, is_test,
                            create_uid, write_uid, create_date, write_date
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                                'pending', 0, FALSE, %s, %s, %s, %s)
                        ON CONFLICT (meta_leadgen_id) DO NOTHING
                        RETURNING id
                    """,
                    (
                        connection.id,
                        connection.company_id.id,
                        leadgen_id,
                        str(event_data.get("page_id") or connection.page_id),
                        str(event_data.get("form_id") or "") or None,
                        str(event_data.get("ad_id") or "") or None,
                        self._parse_meta_datetime(event_data.get("created_time"))
                        or None,
                        raw_payload if connection.store_payloads else None,
                        self.env.uid,
                        self.env.uid,
                        now,
                        now,
                    ),
                    log_exceptions=False,
                )
                row = self.env.cr.fetchone()
        except pg_errors.SerializationFailure:
            # The current REPEATABLE READ snapshot cannot see the transaction
            # which just won the race. Confirm the exact ID from a fresh
            # snapshot; re-raise if the error came from anything else so no
            # webhook can be silently acknowledged without an event row.
            with self.env.registry.cursor() as fresh_cr:
                fresh_cr.execute(
                    """
                        SELECT id
                          FROM meta_lead_event
                         WHERE meta_leadgen_id = %s
                         LIMIT 1
                    """,
                    (leadgen_id,),
                )
                concurrent_row = fresh_cr.fetchone()
            if not concurrent_row:
                raise
            return self.sudo().browse(concurrent_row[0]), False
        if row:
            return self.sudo().browse(row[0]), True
        event = self.sudo().search([("meta_leadgen_id", "=", leadgen_id)], limit=1)
        return event, False

    @api.model
    def _cron_process_queue(self):
        # One call gives the complete cron run a single transaction time budget.
        return self._process_queue()

    @api.model
    def _process_queue(self, connection=None):
        deadline = time.monotonic() + _QUEUE_TIME_BUDGET_SECONDS
        connections = connection or self.env["meta.lead.connection"].sudo().search(
            [("active", "=", True), ("webhook_enabled", "=", True)]
        )
        connections = connections.sudo().filtered(
            lambda current: current._environment_allowed()
        )
        remaining = {
            current.id: min(max(current.processing_batch_size, 1), 500)
            for current in connections
        }
        # Small round-robin slices keep one slow Page from starving the rest of
        # the connections during the shared cron time budget.
        while connections and time.monotonic() < deadline:
            selected_any = False
            for current in connections:
                limit = min(remaining[current.id], _QUEUE_SLICE_SIZE)
                if limit <= 0:
                    continue
                self.env.cr.execute(
                    """
                        SELECT id
                          FROM meta_lead_event
                         WHERE connection_id = %s
                           AND state IN ('pending', 'retry')
                           AND (next_retry_at IS NULL OR next_retry_at <= NOW() AT TIME ZONE 'UTC')
                         ORDER BY create_date, id
                           FOR UPDATE SKIP LOCKED
                         LIMIT %s
                    """,
                    (current.id, limit),
                )
                event_ids = [row[0] for row in self.env.cr.fetchall()]
                if not event_ids:
                    remaining[current.id] = 0
                    continue
                selected_any = True
                remaining[current.id] -= len(event_ids)
                for event in self.browse(event_ids):
                    if time.monotonic() >= deadline:
                        return True
                    try:
                        with self.env.cr.savepoint():
                            event._process_one()
                    except Exception as exc:
                        _logger.error(
                            "Meta event queue isolated an unexpected event failure id=%s type=%s",
                            event.id,
                            type(exc).__name__,
                        )
            if not selected_any or not any(remaining.values()):
                break
        return True

    def _process_one(self, reset_attempts=False):
        self.ensure_one()
        # The queue already locks batches, but manual buttons can race with it.
        # Re-acquiring the row with SKIP LOCKED makes every entry point safe and
        # avoids waiting for a slow Graph API request running in another worker.
        self.flush_recordset(["state", "attempts", "crm_lead_id", "is_test"])
        self.env.cr.execute(
            """
                SELECT state, attempts
                  FROM meta_lead_event
                 WHERE id = %s
                   FOR UPDATE SKIP LOCKED
            """,
            (self.id,),
        )
        locked_row = self.env.cr.fetchone()
        if not locked_row:
            return self.env["crm.lead"]
        locked_state, locked_attempts = locked_row
        self.invalidate_recordset(
            ["state", "attempts", "crm_lead_id", "is_test"], flush=False
        )
        if locked_state == "done":
            return self.crm_lead_id
        if reset_attempts and locked_state not in ("failed", "retry"):
            return self.env["crm.lead"]
        if locked_state not in ("pending", "retry", "failed", "processing"):
            return self.env["crm.lead"]
        connection = self.connection_id.sudo()
        if not self.is_test and not connection._environment_allowed():
            raise UserError(
                _(
                    "Real Meta lead processing is blocked on this Odoo.sh staging/development "
                    "database. Enable the non-production option only for a dedicated test page."
                )
            )
        now = fields.Datetime.now()
        self.sudo().write(
            {
                "state": "processing",
                "attempts": 1 if reset_attempts else locked_attempts + 1,
                "last_attempt_at": now,
                "next_retry_at": False,
                "last_error": False,
            }
        )
        try:
            # Roll back partial attribution/CRM writes before recording a retry.
            # This also recovers a PostgreSQL transaction aborted by a constraint.
            with self.env.cr.savepoint():
                if self.is_test:
                    payload = json.loads(self.lead_payload or "{}")
                else:
                    payload = connection._fetch_lead_details(self.meta_leadgen_id)
                payload_id = str(payload.get("id") or self.meta_leadgen_id)
                if payload_id != self.meta_leadgen_id:
                    raise MetaGraphAPIError(
                        _("Meta returned a different Lead ID."), retryable=False
                    )

                form_id = str(payload.get("form_id") or self.form_id or "") or False
                form_name = payload.get("form_name") or self.form_name
                if form_id and not form_name and not self.is_test:
                    form_name = connection._fetch_form_name(form_id)

                event_values = {
                    "form_id": form_id,
                    "form_name": form_name,
                    "campaign_id": str(payload.get("campaign_id") or "") or False,
                    "campaign_name": payload.get("campaign_name") or False,
                    "adset_id": str(payload.get("adset_id") or "") or False,
                    "adset_name": payload.get("adset_name") or False,
                    "ad_id": str(payload.get("ad_id") or self.ad_id or "") or False,
                    "ad_name": payload.get("ad_name") or False,
                    "platform": payload.get("platform") or False,
                    "meta_created_time": self._parse_meta_datetime(payload.get("created_time"))
                    or self.meta_created_time,
                }
                self.sudo().write(event_values)
                lead, result = self._create_or_link_crm_lead(payload)
                stored_payload = (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True)
                    if connection.store_payloads or self.is_test
                    else False
                )
                self.sudo().write(
                    {
                        "state": "done",
                        "result": result,
                        "crm_lead_id": lead.id,
                        "processed_at": fields.Datetime.now(),
                        "last_error": False,
                        "next_retry_at": False,
                        "lead_payload": stored_payload,
                    }
                )
                return lead
        except MetaGraphAPIError as exc:
            self._record_failure(exc, retryable=exc.retryable)
        except Exception as exc:  # keep unexpected mapping failures visible and recoverable
            _logger.error(
                "Unexpected failure while processing Meta lead event id=%s type=%s",
                self.id,
                type(exc).__name__,
            )
            self._record_failure(exc, retryable=True)
        return self.env["crm.lead"]

    def _record_failure(self, error, *, retryable):
        self.ensure_one()
        connection = self.connection_id.sudo()
        message = connection._sanitized_error(str(error))[:4000]
        should_retry = retryable and self.attempts < connection.max_retries
        values = {"last_error": message}
        if should_retry:
            delay_minutes = min(2 ** max(self.attempts - 1, 0), 60)
            values.update(
                {
                    "state": "retry",
                    "next_retry_at": fields.Datetime.now() + timedelta(minutes=delay_minutes),
                }
            )
        else:
            values.update({"state": "failed", "next_retry_at": False})
        self.sudo().write(values)
        if not should_retry:
            try:
                with self.env.cr.savepoint():
                    connection.message_post(
                        body=Markup("<p>%s</p><p><strong>%s</strong> %s</p>")
                        % (
                            escape(_("A Meta lead event failed permanently.")),
                            escape(_("Lead ID:")),
                            escape(self.meta_leadgen_id),
                        )
                    )
            except Exception:  # a chatter failure must never block the queue
                _logger.warning(
                    "Could not post permanent Meta event failure notice id=%s", self.id
                )

    def _field_values(self, payload):
        self.ensure_one()
        connection = self.connection_id
        answers = field_data_to_dict(payload.get("field_data"))
        first_name = first_field_value(answers, "first_name", default="")
        last_name = first_field_value(answers, "last_name", default="")
        full_name = first_field_value(answers, "full_name", default="") or " ".join(
            part for part in (first_name, last_name) if part
        ).strip()
        standard = {
            "contact_name": full_name or False,
            "email_from": first_field_value(answers, "email", "email_address", default=False),
            "phone": first_field_value(
                answers, "phone_number", "phone", "mobile", default=False
            ),
            "partner_name": first_field_value(
                answers, "company_name", "company", default=False
            ),
            "function": first_field_value(answers, "job_title", default=False),
            "street": first_field_value(answers, "street_address", "street", default=False),
            "city": first_field_value(answers, "city", default=False),
            "zip": first_field_value(
                answers, "zip_code", "post_code", "postal_code", default=False
            ),
        }
        mapped_names = set()
        for mapping in connection.field_mapping_ids.filtered("active").sorted("sequence"):
            values = answers.get(mapping.meta_field_name_normalized) or []
            if not values:
                continue
            value = ", ".join(str(item) for item in values if item not in (None, ""))
            if value:
                standard[mapping.target_field] = value
                mapped_names.add(mapping.meta_field_name_normalized)
        return {key: value for key, value in standard.items() if value}, answers, mapped_names

    def _answers_html(self, answers, mapped_names=None):
        mapped_names = mapped_names or set()
        custom = [
            (name, values)
            for name, values in answers.items()
            if name not in STANDARD_FIELD_NAMES and name not in mapped_names
        ]
        if not custom:
            return False
        rows = []
        for name, values in custom:
            label = name.replace("_", " ").strip().title()
            text = ", ".join(str(value) for value in values if value not in (None, ""))
            if text:
                rows.append(
                    Markup("<li><strong>%s:</strong> %s</li>") % (escape(label), escape(text))
                )
        if not rows:
            return False
        return Markup("<p><strong>%s</strong></p><ul>%s</ul>") % (
            escape(_("Meta form answers")),
            Markup("").join(rows),
        )

    def _find_duplicate_lead(self, values):
        self.ensure_one()
        policy = self.connection_id.duplicate_policy
        if policy == "meta_only":
            return self.env["crm.lead"]
        email = normalize_email(values.get("email_from"))
        phone = normalize_phone(values.get("phone"))
        contact_terms = []
        if policy in ("email", "email_or_phone") and email:
            contact_terms.append(("meta_normalized_email", "=", email))
        if policy in ("phone", "email_or_phone") and phone:
            contact_terms.append(("meta_normalized_phone", "=", phone))
        if not contact_terms:
            return self.env["crm.lead"]
        if len(contact_terms) == 1:
            contact_domain = contact_terms
        else:
            contact_domain = ["|"] + contact_terms
        domain = [
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
            ("stage_id.is_won", "=", False),
        ] + contact_domain
        return self.env["crm.lead"].sudo().search(domain, order="create_date desc, id desc", limit=1)

    def _lock_duplicate_contacts(self, values):
        """Serialize optional contact matching without imposing false SQL uniqueness."""
        self.ensure_one()
        policy = self.connection_id.duplicate_policy
        keys = []
        email = normalize_email(values.get("email_from"))
        phone = normalize_phone(values.get("phone"))
        if policy in ("email", "email_or_phone") and email:
            keys.append(f"{self.company_id.id}:email:{email}")
        if policy in ("phone", "email_or_phone") and phone:
            keys.append(f"{self.company_id.id}:phone:{phone}")
        # Sorted acquisition prevents deadlocks when both email and phone apply.
        for key in sorted(keys):
            digest = hashlib.sha256(key.encode("utf-8")).digest()[:8]
            lock_id = int.from_bytes(digest, byteorder="big", signed=True)
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))

    def _utm_values(self, payload):
        source = self.env.ref("meta_lead_ads_crm.utm_source_meta_lead_ads", raise_if_not_found=False)
        platform = str(payload.get("platform") or "").lower()
        if "instagram" in platform:
            medium = self.env.ref(
                "meta_lead_ads_crm.utm_medium_instagram_lead_ads", raise_if_not_found=False
            )
        elif "facebook" in platform:
            medium = self.env.ref(
                "meta_lead_ads_crm.utm_medium_facebook_lead_ads", raise_if_not_found=False
            )
        else:
            medium = self.env.ref(
                "meta_lead_ads_crm.utm_medium_meta_lead_ads", raise_if_not_found=False
            )
        values = {
            "source_id": source.id if source else False,
            "medium_id": medium.id if medium else False,
        }
        campaign_name = str(payload.get("campaign_name") or "").strip()
        if campaign_name:
            campaign = self.env["utm.campaign"].sudo().search(
                [("name", "=", campaign_name)], limit=1
            )
            if not campaign:
                campaign = self.env["utm.campaign"].sudo().create({"name": campaign_name})
            values["campaign_id"] = campaign.id
        return {key: value for key, value in values.items() if value}

    def _attribution_values(self, payload):
        self.ensure_one()
        return {
            "meta_connection_id": self.connection_id.id,
            "meta_leadgen_id": self.meta_leadgen_id,
            "meta_page_id": self.page_id,
            "meta_page_name": self.connection_id.page_name,
            "meta_form_id": self.form_id,
            "meta_form_name": self.form_name,
            "meta_campaign_id": self.campaign_id,
            "meta_campaign_name": self.campaign_name,
            "meta_adset_id": self.adset_id,
            "meta_adset_name": self.adset_name,
            "meta_ad_id": self.ad_id,
            "meta_ad_name": self.ad_name,
            "meta_platform": self.platform,
            "meta_created_time": self.meta_created_time,
            "meta_is_organic": bool(payload.get("is_organic")),
        }

    def _create_or_link_crm_lead(self, payload):
        self.ensure_one()
        connection = self.connection_id.sudo()
        values, answers, mapped_names = self._field_values(payload)
        answer_html = self._answers_html(answers, mapped_names)
        title_contact = values.get("contact_name") or values.get("partner_name")
        values.update(
            {
                "name": _("%(contact)s - Meta")
                % {"contact": title_contact or _("Meta Lead %(id)s") % {"id": self.meta_leadgen_id}},
                "type": connection.lead_type,
                "company_id": connection.company_id.id,
            }
        )
        if answer_html:
            values["description"] = str(answer_html)
        values.update(self._utm_values(payload))
        values.update(self._attribution_values(payload))

        team, salesperson = connection._routing_assignment(payload)
        if team:
            values["team_id"] = team.id
        if salesperson:
            values["user_id"] = salesperson.id
        if connection.tag_id:
            values["tag_ids"] = [(4, connection.tag_id.id)]

        self._lock_duplicate_contacts(values)
        existing = self._find_duplicate_lead(values)
        if existing:
            updates = {}
            if connection.update_missing_on_duplicate:
                for field_name in (
                    "contact_name",
                    "email_from",
                    "phone",
                    "partner_name",
                    "function",
                    "street",
                    "city",
                    "zip",
                ):
                    if values.get(field_name) and not existing[field_name]:
                        updates[field_name] = values[field_name]
            if not existing.meta_leadgen_id:
                updates.update(self._attribution_values(payload))
            if connection.tag_id and connection.tag_id not in existing.tag_ids:
                updates["tag_ids"] = [(4, connection.tag_id.id)]
            if updates:
                existing.sudo().write(updates)
            note = Markup("<p><strong>%s</strong> %s</p>") % (
                escape(_("New Meta submission linked.")),
                escape(_("Meta Lead ID: %s") % self.meta_leadgen_id),
            )
            if answer_html:
                note += answer_html
            existing.sudo().message_post(body=note)
            return existing, "linked"

        lead = (
            self.env["crm.lead"]
            .sudo()
            .with_company(connection.company_id)
            .create(values)
        )
        return lead, "created"

    def action_process_now(self):
        for event in self:
            if event.state != "done":
                with self.env.cr.savepoint():
                    event.sudo()._process_one()
        if len(self) == 1 and self.crm_lead_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "crm.lead",
                "res_id": self.crm_lead_id.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    def action_retry(self):
        for event in self:
            with self.env.cr.savepoint():
                event.sudo()._process_one(reset_attempts=True)
        if len(self) == 1 and self.crm_lead_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "crm.lead",
                "res_id": self.crm_lead_id.id,
                "view_mode": "form",
                "target": "current",
            }
        return True

    @api.model
    def _cron_purge_payloads(self):
        connections = self.env["meta.lead.connection"].sudo().search([])
        now = fields.Datetime.now()
        for connection in connections:
            cutoff = now - timedelta(days=connection.payload_retention_days)
            if connection.payload_retention_days == 0:
                age_domain = [("state", "in", ("done", "failed", "ignored"))]
            else:
                age_domain = [("create_date", "<=", cutoff)]
            events = self.sudo().search(
                [
                    ("connection_id", "=", connection.id),
                    *age_domain,
                    "|",
                    ("webhook_payload", "!=", False),
                    ("lead_payload", "!=", False),
                ],
                limit=1000,
            )
            if events:
                events.write({"webhook_payload": False, "lead_payload": False})
        return True
