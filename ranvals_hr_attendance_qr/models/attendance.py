import base64
import hashlib
import hmac
import io
import secrets
from datetime import timedelta

import pytz
import qrcode

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import SQL

from ..security_utils import (
    DUMMY_PIN_HASH,
    TOKEN_RE,
    digest_token,
    hash_pin,
    is_valid_pin_hash,
    is_allowed_ip,
    normalize_code,
    normalized_ip,
    parse_networks,
    pin_needs_rehash,
    sign_intent,
    validate_base_url,
    verify_intent,
    verify_pin,
)


ADMIN = "hr_attendance.group_hr_attendance_manager"
SECRET_GROUP = "base.group_no_one"
RATE_WINDOW_MINUTES = 15
IP_ATTEMPT_LIMIT = 1200
CREDENTIAL_ATTEMPT_LIMIT = 5
UNKNOWN_ATTEMPT_LIMIT = 30


def require_admin(records, operation="write"):
    if not records.env.user.has_group(ADMIN):
        raise AccessError(_("Bu işlem için Giriş/Çıkış yöneticisi yetkisi gerekir."))
    records.check_access(operation)


class RanvalsQRStation(models.Model):
    _name = "ranvals.qr.station"
    _description = "Güvenli QR Giriş Noktası"
    _order = "company_id, name, id"
    _check_company_auto = True

    name = fields.Char(
        string="Giriş Noktası Adı",
        required=True,
        default="Personel Giriş–Çıkış Noktası",
    )
    brand_name = fields.Char(
        string="Görünen Kurum Adı",
        required=True,
        default=lambda self: self.env.company.name,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Şirket",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)
    enabled = fields.Boolean(string="QR Giriş–Çıkış Açık", default=False)
    base_url = fields.Char(
        string="Odoo HTTPS Adresi",
        required=True,
        default=lambda self: self.env["ir.config_parameter"].sudo().get_param("web.base.url"),
    )
    allowed_networks = fields.Text(
        string="İzin Verilen İnternet IP / Ağları",
        help=(
            "Her satıra bir genel IP veya CIDR girin. Yerel 192.168.x.x adresi ya da "
            "Odoo sunucu IP'si değil, işletmenin internet çıkış IP'si kullanılmalıdır. "
            "Boş bırakılırsa erişim kapalıdır."
        ),
    )
    timezone = fields.Char(
        string="Saat Dilimi",
        required=True,
        default=lambda self: self.env.company.resource_calendar_id.tz or "Europe/Istanbul",
    )
    remember_days = fields.Integer(string="Telefonu Hatırlama (Gün)", default=30, required=True)
    remember_idle_hours = fields.Integer(
        string="Hatırlanan Oturum Boşta Kalma Sınırı (Saat)",
        default=168,
        required=True,
    )
    temporary_session_minutes = fields.Integer(
        string="Geçici Oturum Süresi (Dakika)",
        default=30,
        required=True,
    )
    max_devices_per_employee = fields.Integer(
        string="Personel / Giriş Noktası Başına Azami Telefon",
        default=5,
        required=True,
    )
    cooldown_seconds = fields.Integer(
        string="İki İşlem Arası Asgari Saniye",
        default=60,
        required=True,
    )
    stale_hours = fields.Integer(
        string="Açık Kayıt Kontrol Sınırı (Saat)",
        default=16,
        required=True,
    )
    log_ip_address = fields.Boolean(
        string="Başarılı İşlemde IP Adresini Sakla",
        default=False,
        help="Kapalıyken IP yalnız ağ doğrulaması ve özetlenmiş hız limiti için kullanılır; mesai kaydına yazılmaz.",
    )
    token = fields.Char(
        default=lambda self: secrets.token_urlsafe(32),
        required=True,
        copy=False,
        readonly=True,
        groups=SECRET_GROUP,
    )
    signing_key = fields.Char(
        default=lambda self: secrets.token_hex(32),
        required=True,
        copy=False,
        readonly=True,
        groups=SECRET_GROUP,
    )
    kiosk_url = fields.Char(
        string="Kapıya Asılacak QR Bağlantısı", compute="_compute_qr", compute_sudo=True
    )
    qr_image = fields.Binary(
        string="QR Kod", compute="_compute_qr", compute_sudo=True, attachment=False
    )

    _token_unique = models.Constraint("UNIQUE(token)", "QR bağlantısı benzersiz olmalıdır.")
    _positive_limits = models.Constraint(
        "CHECK(remember_days > 0 AND remember_idle_hours > 0 "
        "AND temporary_session_minutes > 0 AND max_devices_per_employee > 0 "
        "AND cooldown_seconds > 0 AND stale_hours > 0)",
        "QR güvenlik süreleri sıfırdan büyük olmalıdır.",
    )

    @api.depends("token", "base_url")
    def _compute_qr(self):
        for station in self:
            if not station.token or not station.base_url:
                station.kiosk_url = False
                station.qr_image = False
                continue
            station.kiosk_url = (
                f"{station.base_url.rstrip('/')}/ranvals/qr-attendance/{station.token}"
            )
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(station.kiosk_url)
            qr.make(fit=True)
            image = qr.make_image(fill_color="black", back_color="white")
            output = io.BytesIO()
            image.save(output, format="PNG")
            station.qr_image = base64.b64encode(output.getvalue())

    @api.constrains(
        "allowed_networks",
        "base_url",
        "timezone",
        "remember_days",
        "remember_idle_hours",
        "temporary_session_minutes",
        "max_devices_per_employee",
        "cooldown_seconds",
        "stale_hours",
        "enabled",
        "token",
        "signing_key",
    )
    def _validate_settings(self):
        for station in self:
            try:
                networks = parse_networks(station.allowed_networks)
                validate_base_url(station.base_url)
                pytz.timezone(station.timezone)
            except (ValueError, pytz.UnknownTimeZoneError) as exc:
                raise ValidationError(str(exc)) from exc
            if station.enabled and not networks:
                raise ValidationError(_("Önce izin verilen internet IP adresini tanımlayın."))
            if not 1 <= station.remember_days <= 90:
                raise ValidationError(_("Telefon hatırlama süresi 1–90 gün olmalıdır."))
            if not 1 <= station.remember_idle_hours <= 720:
                raise ValidationError(_("Boşta kalma sınırı 1–720 saat olmalıdır."))
            if not 5 <= station.temporary_session_minutes <= 120:
                raise ValidationError(_("Geçici oturum süresi 5–120 dakika olmalıdır."))
            if not 1 <= station.max_devices_per_employee <= 20:
                raise ValidationError(_("Personel başına telefon sınırı 1–20 olmalıdır."))
            if not 10 <= station.cooldown_seconds <= 600:
                raise ValidationError(_("İki işlem arası süre 10–600 saniye olmalıdır."))
            if not 4 <= station.stale_hours <= 36:
                raise ValidationError(_("Açık kayıt kontrol sınırı 4–36 saat olmalıdır."))
            if not TOKEN_RE.fullmatch(station.token or ""):
                raise ValidationError(_("QR bağlantı anahtarı geçersiz."))
            try:
                signing_key = bytes.fromhex(station.signing_key or "")
            except ValueError as exc:
                raise ValidationError(_("İşlem imza anahtarı geçersiz.")) from exc
            if len(signing_key) != 32:
                raise ValidationError(_("İşlem imza anahtarı 256 bit olmalıdır."))

    @api.model_create_multi
    def create(self, vals_list):
        protected = {"token", "signing_key"}
        if not self.env.su and any(protected.intersection(vals) for vals in vals_list):
            raise AccessError(_("Sunucu güvenlik anahtarları dışarıdan belirlenemez."))
        return super().create(vals_list)

    def write(self, vals):
        vals = dict(vals)
        protected = {"token", "signing_key"}
        if not self.env.su and protected.intersection(vals):
            raise AccessError(_("Sunucu güvenlik anahtarları dışarıdan değiştirilemez."))
        if "company_id" in vals and any(station.company_id.id != vals["company_id"] for station in self):
            raise UserError(_("Şirketi değiştirmek yerine ayrı bir QR giriş noktası oluşturun."))
        result = super().write(vals)
        session_policy = {
            "enabled",
            "active",
            "token",
            "signing_key",
            "base_url",
            "allowed_networks",
            "remember_days",
            "remember_idle_hours",
            "temporary_session_minutes",
            "max_devices_per_employee",
        }
        if session_policy.intersection(vals):
            self.env["ranvals.qr.device"].sudo().search(
                [("station_id", "in", self.ids)]
            ).unlink()
        return result

    def action_add_current_network(self):
        self.ensure_one()
        require_admin(self)
        if not request or not request.httprequest:
            raise UserError(_("Bu işlemi Odoo tarayıcı ekranından çalıştırın."))
        value = request.httprequest.remote_addr
        try:
            address = normalized_ip(value)
            if not address.is_global:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise UserError(
                _("Genel istemci IP adresi tespit edilemedi. Reverse proxy ayarını kontrol edin.")
            ) from exc
        item = f"{address}/{32 if address.version == 4 else 128}"
        current = (self.allowed_networks or "").strip()
        if item not in current.splitlines():
            self.allowed_networks = "\n".join(filter(None, [current, item]))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Ağ adresi eklendi"),
                "message": item,
                "type": "success",
                "sticky": True,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_rotate_qr(self):
        self.ensure_one()
        require_admin(self)
        self.sudo().write(
            {"token": secrets.token_urlsafe(32), "signing_key": secrets.token_hex(32)}
        )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_kiosk(self):
        self.ensure_one()
        require_admin(self, "read")
        return {"type": "ir.actions.act_url", "url": self.kiosk_url, "target": "new"}

    def action_print_qr(self):
        self.ensure_one()
        require_admin(self, "read")
        return {
            "type": "ir.actions.act_url",
            "url": f"/ranvals/qr-attendance/print/{self.id}",
            "target": "new",
        }

    def _assert_network(self, ip):
        self.ensure_one()
        if not self.active or not self.enabled:
            raise UserError(_("Bu giriş noktası kapalı. Yöneticinize başvurun."))
        if not is_allowed_ip(ip, self.allowed_networks):
            raise AccessError(
                _("İşletmenin izin verilen ağına bağlanın. Farklı ağdan işlem yapılamaz.")
            )

    def _assert_credential(self, credential):
        self.ensure_one()
        if (
            not credential
            or not credential.active
            or not credential.employee_id.active
            or credential.company_id != self.company_id
            or credential.employee_id.company_id != self.company_id
            or not credential.pin_hash
        ):
            raise AccessError(_("Personel kimliği doğrulanamadı. Yeniden giriş yapın."))

    def _consume_rate_bucket(self, kind, value, maximum):
        now = fields.Datetime.now()
        cutoff = now - timedelta(minutes=RATE_WINDOW_MINUTES)
        key = hmac.new(
            self.signing_key.encode("ascii"),
            f"{kind}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        Rate = self.env["ranvals.qr.rate"].sudo()
        Rate.flush_model()
        self.env.cr.execute(
            SQL(
                """INSERT INTO ranvals_qr_rate
                       (station_id, company_id, key_digest, window_start, attempts,
                        create_uid, write_uid, create_date, write_date)
                   VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s)
                   ON CONFLICT (station_id, key_digest) DO UPDATE SET
                     window_start = CASE WHEN ranvals_qr_rate.window_start <= %s
                                         THEN %s ELSE ranvals_qr_rate.window_start END,
                     attempts = CASE WHEN ranvals_qr_rate.window_start <= %s
                                     THEN 1 ELSE LEAST(ranvals_qr_rate.attempts + 1, %s) END,
                     write_date = %s
                   RETURNING id, attempts""",
                self.id,
                self.company_id.id,
                key,
                now,
                self.env.uid,
                self.env.uid,
                now,
                now,
                cutoff,
                now,
                cutoff,
                maximum + 1,
                now,
            )
        )
        rate_id, attempts = self.env.cr.fetchone()
        Rate.browse(rate_id).invalidate_recordset()
        return attempts <= maximum

    def _rate_attempt(self, credential, ip):
        canonical_ip = str(normalized_ip(ip))
        if not self._consume_rate_bucket("ip", canonical_ip, IP_ATTEMPT_LIMIT):
            return False
        if credential:
            return credential._consume_auth_attempt(CREDENTIAL_ATTEMPT_LIMIT)
        return self._consume_rate_bucket("credential", "unknown", UNKNOWN_ATTEMPT_LIMIT)

    def _reset_credential_rate(self, credential):
        credential._reset_auth_attempts()

    def _authenticate_pin(self, code, pin, ip, remember=False, replace_device=None):
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute(
            SQL("SELECT id FROM ranvals_qr_station WHERE id = %s FOR SHARE", self.id)
        )
        if not self.env.cr.fetchone():
            raise AccessError(_("QR giriş noktası artık mevcut değil."))
        self.invalidate_recordset()
        self._assert_network(ip)
        code = normalize_code(code)
        Credential = self.env["ranvals.qr.credential"].sudo().with_company(self.company_id)
        credential = Credential.search(
            [("company_id", "=", self.company_id.id), ("code", "=", code)], limit=1
        )
        if credential:
            credential.flush_recordset()
            self.env.cr.execute(
                SQL(
                    "SELECT id FROM ranvals_qr_credential WHERE id = %s FOR UPDATE",
                    credential.id,
                )
            )
            if not self.env.cr.fetchone():
                credential = Credential
            else:
                credential.invalidate_recordset()
        if not self._rate_attempt(credential, ip):
            raise AccessError(_("Çok fazla deneme yapıldı. 15 dakika sonra tekrar deneyin."))
        stored_hash = (
            credential.pin_hash
            if credential and is_valid_pin_hash(credential.pin_hash)
            else DUMMY_PIN_HASH
        )
        checked = verify_pin(pin, stored_hash)
        if (
            not credential
            or credential.code != code
            or not checked
            or not credential.active
            or not credential.employee_id.active
        ):
            raise AccessError(_("Personel kodu veya PIN hatalı."))
        self._assert_credential(credential)
        self._reset_credential_rate(credential)

        if pin_needs_rehash(credential.pin_hash):
            credential._set_pin(pin)
        if replace_device:
            replace_device.sudo().unlink()

        Device = self.env["ranvals.qr.device"].sudo()
        now = fields.Datetime.now()
        stale_devices = Device.search(
            [
                ("credential_id", "=", credential.id),
                "|",
                ("expires_at", "<=", now),
                ("credential_version", "!=", credential.version_key),
            ]
        )
        stale_devices.unlink()
        existing = Device.search(
            [
                ("credential_id", "=", credential.id),
                ("station_id", "=", self.id),
            ],
            order="last_seen_at asc, id asc",
        )
        overflow = len(existing) - self.max_devices_per_employee + 1
        if overflow > 0:
            existing[:overflow].unlink()

        raw_token = secrets.token_urlsafe(32)
        lifetime = (
            timedelta(days=self.remember_days)
            if remember
            else timedelta(minutes=self.temporary_session_minutes)
        )
        device = Device.create(
            {
                "station_id": self.id,
                "credential_id": credential.id,
                "token_digest": digest_token(raw_token),
                "expires_at": now + lifetime,
                "last_seen_at": now,
                "remembered": bool(remember),
                "credential_version": credential.version_key,
            }
        )
        return device, raw_token

    def _device_from_token(self, token, *, touch=True):
        self.ensure_one()
        Device = self.env["ranvals.qr.device"].sudo()
        if not token or not TOKEN_RE.fullmatch(token):
            return Device
        device = Device.search(
            [("station_id", "=", self.id), ("token_digest", "=", digest_token(token))],
            limit=1,
        )
        if not device:
            return Device
        now = fields.Datetime.now()
        if not device._valid_for(self, now=now):
            device.unlink()
            return Device
        if touch and device.last_seen_at < now - timedelta(minutes=5):
            device.last_seen_at = now
        return device

    def _last_attendance(self, credential):
        return (
            self.env["hr.attendance"]
            .sudo()
            .with_company(self.company_id)
            .search(
                [("employee_id", "=", credential.employee_id.id)],
                order="check_in desc, id desc",
                limit=1,
            )
        )

    def _snapshot(self, last):
        return {
            "last_id": last.id or 0,
            "last_in": fields.Datetime.to_string(last.check_in) if last else "",
            "last_out": (
                fields.Datetime.to_string(last.check_out) if last and last.check_out else ""
            ),
        }

    def _state_review(self, credential, last, now):
        if last and (last.check_in > now or (last.check_out and last.check_out > now)):
            return _("Gelecek tarihli bir mesai kaydı bulundu. Yönetici kontrolü gerekiyor.")
        open_attendances = self.env["hr.attendance"].sudo().search(
            [("employee_id", "=", credential.employee_id.id), ("check_out", "=", False)],
            limit=2,
        )
        if len(open_attendances) > 1:
            return _("Birden fazla açık mesai kaydı bulundu. Yönetici kontrolü gerekiyor.")
        if open_attendances and open_attendances != last:
            return _("Mesai kayıt sırası tutarsız. Yönetici kontrolü gerekiyor.")
        if last and not last.check_out and last.check_in < now - timedelta(hours=self.stale_hours):
            return _("Önceki giriş uzun süredir açık. Yönetici kontrolü gerekiyor.")
        return False

    def _public_state(self, credential):
        self.ensure_one()
        self._assert_credential(credential)
        last = self._last_attendance(credential)
        now = fields.Datetime.now()
        is_in = bool(last and not last.check_out)
        last_time = (last.check_out or last.check_in) if last else None
        wait = (
            max(0, self.cooldown_seconds - int((now - last_time).total_seconds()))
            if last_time and last_time <= now
            else 0
        )
        review = self._state_review(credential, last, now)
        signed_values = {
            "station_id": self.id,
            "credential_id": credential.id,
            "version": credential.version_key,
            "action": "out" if is_in else "in",
            **self._snapshot(last),
        }
        return {
            "employee_name": credential.employee_id.name,
            "is_in": is_in,
            "last_time": self._format_time(last_time) if last_time else "",
            "wait_seconds": wait,
            "review": review,
            "intent": sign_intent(self.signing_key, signed_values),
        }

    def _format_time(self, value):
        if not value:
            return ""
        aware = pytz.utc.localize(value) if value.tzinfo is None else value.astimezone(pytz.utc)
        return aware.astimezone(pytz.timezone(self.timezone)).strftime("%d.%m.%Y %H:%M:%S")

    def _perform(self, device, intent, ip):
        """Commit one explicit, signed transition after a fixed lock order."""
        self.ensure_one()
        device = device.sudo().exists()
        if not device:
            raise AccessError(_("Telefon oturumunuz kapatılmış. Yeniden giriş yapın."))
        device.ensure_one()
        credential = device.credential_id.sudo()
        employee = credential.employee_id.sudo()

        self.flush_recordset()
        self.env.cr.execute(
            SQL("SELECT id FROM ranvals_qr_station WHERE id = %s FOR SHARE", self.id)
        )
        if not self.env.cr.fetchone():
            raise AccessError(_("QR giriş noktası artık mevcut değil."))
        credential._serialize()
        device.flush_recordset()
        self.env.cr.execute(
            SQL("SELECT id FROM ranvals_qr_device WHERE id = %s FOR UPDATE", device.id)
        )
        if not self.env.cr.fetchone():
            raise AccessError(_("Telefon oturumunuz kapatılmış. Yeniden giriş yapın."))
        self.env.cr.execute(SQL("SELECT id FROM hr_employee WHERE id = %s FOR UPDATE", employee.id))
        if not self.env.cr.fetchone():
            raise AccessError(_("Personel kaydı artık mevcut değil."))

        self.invalidate_recordset()
        credential.invalidate_recordset()
        device.invalidate_recordset()
        employee.invalidate_recordset()
        self._assert_network(ip)
        self._assert_credential(credential)
        if not device._valid_for(self):
            raise AccessError(_("Telefon oturumunuz geçersiz. Yeniden giriş yapın."))

        try:
            payload = verify_intent(self.signing_key, intent)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        if (
            payload.get("station_id") != self.id
            or payload.get("credential_id") != credential.id
            or payload.get("version") != credential.version_key
            or payload.get("action") not in ("in", "out")
        ):
            raise AccessError(_("Bu işlem isteği size ait değil. Sayfayı yenileyin."))

        Operation = self.env["ranvals.qr.operation"].sudo()
        prior = Operation.search(
            [("station_id", "=", self.id), ("nonce", "=", payload["nonce"])], limit=1
        )
        if prior:
            if prior.credential_id != credential:
                raise AccessError(_("İşlem kimliği uyuşmuyor."))
            return prior

        last = self._last_attendance(credential)
        if last:
            last.invalidate_recordset(["check_in", "check_out"])
        if any(payload.get(key) != value for key, value in self._snapshot(last).items()):
            raise UserError(
                _("Giriş–çıkış durumunuz değişmiş. Sayfayı yenileyin; ikinci kayıt oluşturulmadı.")
            )

        now = fields.Datetime.now()
        review = self._state_review(credential, last, now)
        if review:
            raise UserError(review)
        is_in = bool(last and not last.check_out)
        desired = "out" if is_in else "in"
        if desired != payload["action"]:
            raise UserError(_("İşlem mevcut durumunuzla uyuşmuyor. Sayfayı yenileyin."))
        previous_time = (last.check_out or last.check_in) if last else None
        if previous_time and (now - previous_time).total_seconds() < self.cooldown_seconds:
            raise UserError(_("Önceki işlem çok yeni. Lütfen bekleyip sayfayı yenileyin."))

        Attendance = (
            self.env["hr.attendance"]
            .sudo()
            .with_company(self.company_id)
            .with_context(ranvals_qr_internal=True)
        )
        metadata = {"mode": "kiosk"}
        if self.log_ip_address:
            metadata["ip_address"] = str(normalized_ip(ip))
        if desired == "in":
            values = {
                "employee_id": employee.id,
                "check_in": now,
                "ranvals_qr_in_station_id": self.id,
            }
            values.update(
                {f"in_{key}": value for key, value in metadata.items() if f"in_{key}" in Attendance._fields}
            )
            attendance = Attendance.create(values)
        else:
            values = {"check_out": now, "ranvals_qr_out_station_id": self.id}
            values.update(
                {f"out_{key}": value for key, value in metadata.items() if f"out_{key}" in Attendance._fields}
            )
            last.with_env(Attendance.env).write(values)
            attendance = last
        return Operation.create(
            {
                "station_id": self.id,
                "credential_id": credential.id,
                "device_id": device.id,
                "attendance_id": attendance.id,
                "nonce": payload["nonce"],
                "action": desired,
                "event_time": now,
            }
        )


class RanvalsQRCredential(models.Model):
    _name = "ranvals.qr.credential"
    _description = "Güvenli QR Personel Kimliği"
    _rec_name = "employee_id"
    _order = "company_id, employee_id, id"
    _check_company_auto = True

    employee_id = fields.Many2one(
        "hr.employee",
        string="Personel",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Şirket",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(string="QR Erişimi Açık", default=True)
    qr_only = fields.Boolean(
        string="Yalnız Güvenli QR Kanalı",
        default=True,
        help="Açıkken standart Odoo kiosk ve üst çubuk geçişleri engellenir; yönetici elle düzeltme yapabilir.",
    )
    code = fields.Char(
        string="Personel Kodu",
        required=True,
        copy=False,
        default=lambda self: "R" + secrets.token_hex(6).upper(),
    )
    op_revision = fields.Integer(
        default=0, required=True, readonly=True, copy=False, groups=SECRET_GROUP
    )
    pin_hash = fields.Char(string="PIN Özeti", readonly=True, copy=False, groups=SECRET_GROUP)
    pin_ready = fields.Boolean(
        string="PIN Tanımlı", compute="_compute_pin_ready", compute_sudo=True
    )
    version_key = fields.Char(
        default=lambda self: secrets.token_hex(16),
        required=True,
        readonly=True,
        copy=False,
        groups=SECRET_GROUP,
    )
    auth_attempts = fields.Integer(
        default=0, required=True, readonly=True, copy=False, groups=SECRET_GROUP
    )
    auth_window_start = fields.Datetime(
        readonly=True, copy=False, groups=SECRET_GROUP
    )

    _employee_company_unique = models.Constraint(
        "UNIQUE(employee_id, company_id)",
        "Bu personelin bu şirket için QR kimliği zaten var; mevcut kaydı kullanın.",
    )
    _code_unique = models.Constraint(
        "UNIQUE(company_id, code)",
        "Personel kodu bu şirkette benzersiz olmalıdır.",
    )
    _auth_attempts_nonnegative = models.Constraint(
        "CHECK(auth_attempts >= 0)", "Kimlik deneme sayısı negatif olamaz."
    )
    _revision_nonnegative = models.Constraint(
        "CHECK(op_revision >= 0)", "İşlem revizyonu negatif olamaz."
    )

    @api.depends("pin_hash")
    def _compute_pin_ready(self):
        for credential in self:
            credential.pin_ready = bool(credential.pin_hash)

    @api.model_create_multi
    def create(self, vals_list):
        protected = {
            "op_revision",
            "pin_hash",
            "version_key",
            "auth_attempts",
            "auth_window_start",
        }
        if not self.env.su and any(protected.intersection(vals) for vals in vals_list):
            raise AccessError(_("Kimlik güvenlik alanları dışarıdan belirlenemez."))
        normalized = []
        for original in vals_list:
            vals = dict(original)
            if "code" in vals:
                vals["code"] = normalize_code(vals["code"])
            normalized.append(vals)
        return super().create(normalized)

    def write(self, vals):
        vals = dict(vals)
        protected = {
            "op_revision",
            "pin_hash",
            "version_key",
            "auth_attempts",
            "auth_window_start",
        }
        if not self.env.su and protected.intersection(vals):
            raise AccessError(_("Kimlik güvenlik alanları dışarıdan değiştirilemez."))
        if "employee_id" in vals and any(
            credential.employee_id.id != vals["employee_id"] for credential in self
        ):
            raise UserError(_("QR kimliğinin personelini değiştirmeyin; yeni kimlik oluşturun."))
        if "company_id" in vals and any(
            credential.company_id.id != vals["company_id"] for credential in self
        ):
            raise UserError(_("QR kimliğinin şirketini değiştirmeyin; yeni kimlik oluşturun."))
        if "code" in vals:
            vals["code"] = normalize_code(vals["code"])
        result = super().write(vals)
        if {"pin_hash", "active", "code", "company_id", "version_key"}.intersection(vals):
            self.env["ranvals.qr.device"].sudo().search(
                [("credential_id", "in", self.ids)]
            ).unlink()
        return result

    @api.constrains(
        "code", "employee_id", "company_id", "version_key", "pin_hash", "active"
    )
    def _validate_identity(self):
        for credential in self:
            if not normalize_code(credential.code):
                raise ValidationError(
                    _("Personel kodu 4–20 İngilizce harf/rakamdan oluşmalıdır.")
                )
            if credential.active and credential.employee_id.company_id != credential.company_id:
                raise ValidationError(_("Personel ile QR kimliğinin şirketi aynı olmalıdır."))
            if not TOKEN_RE.fullmatch(credential.version_key or ""):
                raise ValidationError(_("Kimlik sürüm anahtarı geçersiz."))
            if credential.pin_hash and not is_valid_pin_hash(credential.pin_hash):
                raise ValidationError(_("PIN özeti biçimi veya güvenlik maliyeti geçersiz."))

    def _serialize(self):
        self.ensure_one()
        self.flush_recordset(["op_revision"])
        self.env.cr.execute(
            SQL(
                "UPDATE ranvals_qr_credential "
                "SET op_revision = op_revision + 1 WHERE id = %s RETURNING id",
                self.id,
            )
        )
        if not self.env.cr.fetchone():
            raise AccessError(_("Personel kimliği artık mevcut değil."))
        self.invalidate_recordset(["op_revision"])

    def _consume_auth_attempt(self, maximum):
        self.ensure_one()
        now = fields.Datetime.now()
        cutoff = now - timedelta(minutes=RATE_WINDOW_MINUTES)
        self.env.cr.execute(
            SQL(
                """UPDATE ranvals_qr_credential SET
                       auth_window_start = CASE
                           WHEN auth_window_start IS NULL OR auth_window_start <= %s
                           THEN %s ELSE auth_window_start END,
                       auth_attempts = CASE
                           WHEN auth_window_start IS NULL OR auth_window_start <= %s
                           THEN 1 ELSE LEAST(auth_attempts + 1, %s) END,
                       write_uid = %s, write_date = %s
                   WHERE id = %s RETURNING auth_attempts""",
                cutoff,
                now,
                cutoff,
                maximum + 1,
                self.env.uid,
                now,
                self.id,
            )
        )
        row = self.env.cr.fetchone()
        if not row:
            raise AccessError(_("Personel kimliği artık mevcut değil."))
        self.invalidate_recordset(["auth_attempts", "auth_window_start", "write_date"])
        return row[0] <= maximum

    def _reset_auth_attempts(self):
        self.ensure_one()
        self.env.cr.execute(
            SQL(
                "UPDATE ranvals_qr_credential SET auth_attempts = 0, "
                "write_uid = %s, write_date = %s WHERE id = %s",
                self.env.uid,
                fields.Datetime.now(),
                self.id,
            )
        )
        self.invalidate_recordset(["auth_attempts", "write_date"])

    def _set_pin(self, pin):
        self.ensure_one()
        self.sudo().write(
            {"pin_hash": hash_pin(pin), "version_key": secrets.token_hex(16)}
        )

    def action_rotate_pin(self):
        self.ensure_one()
        require_admin(self)
        pin = "".join(secrets.choice("0123456789") for _ in range(10))
        self._set_pin(pin)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Yeni kişisel PIN — yalnız bir kez gösterilir"),
                "message": _(
                    "Personel kodu: %(code)s | PIN: %(pin)s",
                    code=self.code,
                    pin=pin,
                ),
                "type": "warning",
                "sticky": True,
            },
        }

    def action_revoke_devices(self):
        require_admin(self)
        for credential in self:
            credential.sudo().write({"version_key": secrets.token_hex(16)})
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("Hatırlanan telefon oturumları kalıcı olarak kapatıldı."),
                "type": "success",
            },
        }


class RanvalsQRDevice(models.Model):
    _name = "ranvals.qr.device"
    _description = "Güvenli QR Telefon Oturumu"
    _rec_name = "credential_id"
    _order = "last_seen_at desc, id desc"
    _check_company_auto = True

    station_id = fields.Many2one(
        "ranvals.qr.station", required=True, index=True, ondelete="cascade", check_company=True
    )
    credential_id = fields.Many2one(
        "ranvals.qr.credential",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(related="station_id.company_id", store=True, index=True)
    token_digest = fields.Char(required=True, readonly=True, groups=SECRET_GROUP)
    credential_version = fields.Char(required=True, readonly=True, groups=SECRET_GROUP)
    remembered = fields.Boolean(required=True, default=False, readonly=True)
    last_seen_at = fields.Datetime(required=True, readonly=True, index=True)
    expires_at = fields.Datetime(required=True, readonly=True, index=True)

    _token_unique = models.Constraint(
        "UNIQUE(token_digest)", "Telefon oturumu benzersiz olmalıdır."
    )

    @api.constrains("station_id", "credential_id", "company_id")
    def _validate_company(self):
        for device in self:
            if (
                device.station_id.company_id != device.credential_id.company_id
                or device.company_id != device.credential_id.company_id
            ):
                raise ValidationError(_("Telefon oturumu, giriş noktası ve personel aynı şirkette olmalıdır."))

    def _valid_for(self, station, *, now=None):
        self.ensure_one()
        now = now or fields.Datetime.now()
        credential = self.credential_id
        idle_expired = bool(
            self.remembered
            and self.last_seen_at
            and self.last_seen_at < now - timedelta(hours=station.remember_idle_hours)
        )
        return bool(
            self.station_id == station
            and self.expires_at > now
            and not idle_expired
            and self.credential_version == credential.version_key
            and credential.active
            and credential.employee_id.active
            and credential.company_id == station.company_id
            and credential.employee_id.company_id == station.company_id
        )

    def action_revoke(self):
        require_admin(self, "unlink")
        self.unlink()
        return {"type": "ir.actions.client", "tag": "reload"}

    @api.model
    def _cleanup_batch(self, *, limit=6000):
        now = fields.Datetime.now()
        per_model = max(1, limit // 3)
        targets = [
            (self.sudo(), [("expires_at", "<=", now)]),
            (
                self.env["ranvals.qr.rate"].sudo(),
                [("window_start", "<", now - timedelta(days=1))],
            ),
            (
                self.env["ranvals.qr.operation"].sudo(),
                [("event_time", "<", now - timedelta(days=90))],
            ),
        ]
        processed = 0
        remaining = 0
        for model, domain in targets:
            records = model.search(domain, limit=per_model)
            processed += len(records)
            records.unlink()
            remaining += model.search_count(domain)
        return processed, remaining

    @api.model
    def _cron_cleanup(self, *, limit=6000):
        processed, remaining = self._cleanup_batch(limit=limit)
        self.env["ir.cron"]._commit_progress(processed, remaining=remaining)


class RanvalsQRRate(models.Model):
    _name = "ranvals.qr.rate"
    _description = "Güvenli QR Deneme Limiti"
    _check_company_auto = True

    station_id = fields.Many2one(
        "ranvals.qr.station", required=True, index=True, ondelete="cascade", check_company=True
    )
    company_id = fields.Many2one(related="station_id.company_id", store=True, index=True)
    key_digest = fields.Char(required=True, groups=SECRET_GROUP)
    window_start = fields.Datetime(required=True, index=True)
    attempts = fields.Integer(required=True, default=0)

    _key_unique = models.Constraint(
        "UNIQUE(station_id, key_digest)", "Deneme sayacı benzersiz olmalıdır."
    )
    _attempts_nonnegative = models.Constraint(
        "CHECK(attempts >= 0)", "Deneme sayısı negatif olamaz."
    )


class RanvalsQROperation(models.Model):
    _name = "ranvals.qr.operation"
    _description = "Güvenli QR İşlem Makbuzu"
    _order = "event_time desc, id desc"
    _check_company_auto = True

    station_id = fields.Many2one(
        "ranvals.qr.station", required=True, index=True, ondelete="restrict", check_company=True
    )
    credential_id = fields.Many2one(
        "ranvals.qr.credential",
        required=True,
        index=True,
        ondelete="restrict",
        check_company=True,
    )
    device_id = fields.Many2one("ranvals.qr.device", index=True, ondelete="set null")
    employee_id = fields.Many2one(related="credential_id.employee_id", store=True, index=True)
    company_id = fields.Many2one(related="station_id.company_id", store=True, index=True)
    attendance_id = fields.Many2one("hr.attendance", index=True, ondelete="set null")
    nonce = fields.Char(required=True, readonly=True, groups=SECRET_GROUP)
    action = fields.Selection(
        [("in", "İşe Giriş"), ("out", "İşten Çıkış")], required=True, readonly=True
    )
    event_time = fields.Datetime(required=True, readonly=True, index=True)

    _nonce_unique = models.Constraint(
        "UNIQUE(station_id, nonce)", "Bu işlem zaten kaydedildi."
    )

    @api.constrains("station_id", "credential_id", "attendance_id", "device_id")
    def _validate_links(self):
        for operation in self:
            company = operation.station_id.company_id
            if operation.credential_id.company_id != company:
                raise ValidationError(_("İşlem makbuzundaki personel ve istasyon şirketi uyuşmuyor."))
            if operation.attendance_id and operation.attendance_id.employee_id != operation.employee_id:
                raise ValidationError(_("İşlem makbuzu farklı bir personelin mesai kaydına bağlanamaz."))
            if operation.device_id and (
                operation.device_id.station_id != operation.station_id
                or operation.device_id.credential_id != operation.credential_id
            ):
                raise ValidationError(_("İşlem makbuzundaki telefon oturumu uyuşmuyor."))


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    ranvals_company_snapshot_id = fields.Many2one(
        "res.company",
        string="Mesai Şirketi (Anlık Görüntü)",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
        groups=ADMIN,
        help="Kayıt oluşturulduğu andaki personel şirketini korur; dönem raporlarında tarihsel şirket ayrımı için kullanılır.",
    )
    ranvals_qr_in_station_id = fields.Many2one(
        "ranvals.qr.station",
        string="QR Giriş Noktası",
        readonly=True,
        copy=False,
        ondelete="restrict",
        groups=ADMIN,
    )
    ranvals_qr_out_station_id = fields.Many2one(
        "ranvals.qr.station",
        string="QR Çıkış Noktası",
        readonly=True,
        copy=False,
        ondelete="restrict",
        groups=ADMIN,
    )

    @api.model
    def _serialize_qr_credentials(self, employee_ids):
        if not employee_ids:
            return
        credentials = self.env["ranvals.qr.credential"].sudo().with_context(
            active_test=False
        ).search([("employee_id", "in", sorted(set(employee_ids)))], order="id")
        for credential in credentials:
            credential._serialize()

    @api.model_create_multi
    def create(self, vals_list):
        provenance = {
            "ranvals_company_snapshot_id",
            "ranvals_qr_in_station_id",
            "ranvals_qr_out_station_id",
        }
        if not self.env.su and any(provenance.intersection(vals) for vals in vals_list):
            raise AccessError(_("QR kaynak alanları yalnız doğrulanmış QR servisi tarafından yazılabilir."))
        employee_ids = {
            vals.get("employee_id") for vals in vals_list if vals.get("employee_id")
        }
        companies = {
            employee.id: employee.company_id.id
            for employee in self.env["hr.employee"].sudo().browse(employee_ids).exists()
        }
        if self.env.su:
            for vals in vals_list:
                if vals.get("employee_id") and not vals.get("ranvals_company_snapshot_id"):
                    vals["ranvals_company_snapshot_id"] = companies.get(vals["employee_id"])
        self._serialize_qr_credentials(
            [vals.get("employee_id") for vals in vals_list if vals.get("employee_id")]
        )
        records = super().create(vals_list)
        # Defaults/imports may resolve employee_id only during ``super``. Fill
        # the immutable company snapshot from the actual created record in all
        # cases, including sudo-created standard attendances.
        for record in records.filtered(
            lambda attendance: attendance.employee_id
            and not attendance.ranvals_company_snapshot_id
        ):
            record.sudo().write(
                {"ranvals_company_snapshot_id": record.employee_id.company_id.id}
            )
        return records

    def write(self, vals):
        provenance = {
            "ranvals_company_snapshot_id",
            "ranvals_qr_in_station_id",
            "ranvals_qr_out_station_id",
        }
        if not self.env.su and provenance.intersection(vals):
            raise AccessError(_("QR kaynak alanları yalnız doğrulanmış QR servisi tarafından yazılabilir."))
        if vals.get("employee_id"):
            new_employee = self.env["hr.employee"].sudo().browse(
                vals["employee_id"]
            ).exists()
            if not new_employee:
                raise ValidationError(_("Seçilen personel bulunamadı."))
            for attendance in self:
                if attendance.employee_id == new_employee:
                    continue
                if attendance.ranvals_qr_in_station_id or attendance.ranvals_qr_out_station_id:
                    raise ValidationError(
                        _("QR kaynaklı bir mesai kaydı başka personele aktarılamaz.")
                    )
                snapshot_company = (
                    attendance.ranvals_company_snapshot_id
                    or attendance.employee_id.company_id
                )
                if snapshot_company != new_employee.company_id:
                    raise ValidationError(
                        _("Mesai kaydı farklı şirketteki bir personele aktarılamaz.")
                    )
        if {"employee_id", "check_in", "check_out"}.intersection(vals):
            employee_ids = self.mapped("employee_id").ids
            if vals.get("employee_id"):
                employee_ids.append(vals["employee_id"])
            self._serialize_qr_credentials(employee_ids)
        result = super().write(vals)
        for attendance in self.filtered(
            lambda item: item.employee_id and not item.ranvals_company_snapshot_id
        ):
            attendance.sudo().write(
                {"ranvals_company_snapshot_id": attendance.employee_id.company_id.id}
            )
        return result

    def unlink(self):
        self._serialize_qr_credentials(self.mapped("employee_id").ids)
        return super().unlink()

    @api.constrains(
        "employee_id",
        "ranvals_company_snapshot_id",
        "ranvals_qr_in_station_id",
        "ranvals_qr_out_station_id",
    )
    def _validate_qr_station_company(self):
        for attendance in self:
            company = attendance.ranvals_company_snapshot_id or attendance.employee_id.company_id
            if attendance.ranvals_qr_in_station_id and attendance.ranvals_qr_in_station_id.company_id != company:
                raise ValidationError(_("QR giriş noktası personelin şirketiyle uyuşmuyor."))
            if attendance.ranvals_qr_out_station_id and attendance.ranvals_qr_out_station_id.company_id != company:
                raise ValidationError(_("QR çıkış noktası personelin şirketiyle uyuşmuyor."))


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def write(self, vals):
        credentials = self.env["ranvals.qr.credential"]
        if {"active", "company_id"}.intersection(vals):
            credentials = credentials.sudo().with_context(active_test=False).search(
                [("employee_id", "in", self.ids)], order="id"
            )
            for credential in credentials:
                credential.write({"version_key": secrets.token_hex(16)})
        result = super().write(vals)
        if "company_id" in vals:
            for credential in credentials:
                # Keep the old company on historical credentials so operation
                # receipts remain internally consistent. A fresh credential is
                # created for the employee in the destination company.
                credential.write({"active": False})
        return result

    def _attendance_action_change(self, geo_information=None):
        self.ensure_one()
        credentials = self.env["ranvals.qr.credential"].sudo().with_context(
            active_test=False
        ).search(
            [("employee_id", "=", self.id)], order="id"
        )
        if not credentials:
            return super()._attendance_action_change(geo_information)
        for credential in credentials:
            credential._serialize()
        credentials.invalidate_recordset()
        if any(credentials.mapped("qr_only")):
            raise UserError(
                _("Bu personel yalnız Güvenli QR Mesai ekranından giriş–çıkış yapabilir.")
            )
        self.env.cr.execute(SQL("SELECT id FROM hr_employee WHERE id = %s FOR UPDATE", self.id))
        now = fields.Datetime.now()
        last = self.env["hr.attendance"].sudo().search(
            [("employee_id", "=", self.id)], order="check_in desc, id desc", limit=1
        )
        previous_time = (last.check_out or last.check_in) if last else None
        stations = self.env["ranvals.qr.station"].sudo().search(
            [
                ("company_id", "=", self.company_id.id),
                ("enabled", "=", True),
                ("active", "=", True),
            ]
        )
        cooldown = max([10, *stations.mapped("cooldown_seconds")])
        if previous_time and previous_time > now:
            raise UserError(_("Gelecek tarihli mesai kaydı bulundu. Yönetici kontrolü gerekiyor."))
        if previous_time and (now - previous_time).total_seconds() < cooldown:
            raise UserError(_("Son giriş–çıkış işlemi çok yeni. Lütfen biraz bekleyin."))
        self.invalidate_recordset(["last_attendance_id", "attendance_state"])
        return super()._attendance_action_change(geo_information)
