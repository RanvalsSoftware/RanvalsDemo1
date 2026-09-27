"""Public endpoints for the isolated QR attendance channel.

The public surface never exposes an employee directory. Every state-changing
request is a CSRF-protected form POST and the network policy is evaluated
again immediately before the attendance transaction.
"""

from odoo import _, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from werkzeug.exceptions import NotFound, UnsupportedMediaType

from ..models.attendance import ADMIN
from ..security_utils import TOKEN_RE


class RanvalsQRAttendanceController(http.Controller):
    def _station(self, token):
        if not TOKEN_RE.fullmatch(token or ""):
            raise NotFound()
        station = (
            request.env["ranvals.qr.station"]
            .sudo()
            .search([("token", "=", token), ("active", "=", True)], limit=1)
        )
        if not station:
            raise NotFound()
        return station.with_company(station.company_id).with_context(
            allowed_company_ids=[station.company_id.id]
        )

    def _cookie_name(self, station):
        return f"__Secure-ranvals_qr_{station.id}"

    def _base_path(self, station):
        return f"/ranvals/qr-attendance/{station.token}"

    def _flash_key(self, station):
        return f"ranvals_qr_flash_{station.id}"

    def _raw_device_token(self, station):
        return request.httprequest.cookies.get(self._cookie_name(station), "")

    def _device(self, station, *, touch=True):
        return station._device_from_token(self._raw_device_token(station), touch=touch)

    def _network(self, station):
        if request.httprequest.scheme != "https":
            raise AccessError(
                _("Güvenli HTTPS bağlantısı kullanın. HTTP üzerinden giriş–çıkış kapalıdır.")
            )
        station._assert_network(request.httprequest.remote_addr)
        return request.httprequest.remote_addr

    def _require_form_content_type(self):
        if request.httprequest.mimetype != "application/x-www-form-urlencoded":
            raise UnsupportedMediaType()

    def _response_headers(self, response):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def _expire_cookie(self, response, station):
        response.delete_cookie(
            self._cookie_name(station),
            path=self._base_path(station),
            secure=True,
            httponly=True,
            samesite="Lax",
        )

    def _render(self, station, error=None, denied=False, status=200):
        info = False
        raw_token = self._raw_device_token(station)
        valid_device = False
        if not denied:
            valid_device = self._device(station)
            if valid_device:
                try:
                    info = station._public_state(valid_device.credential_id)
                except (AccessError, UserError):
                    info = False
        values = {
            "brand_name": station.brand_name,
            "station_name": station.name,
            "path": self._base_path(station),
            "info": info,
            "error": error,
            "denied": denied,
            "observed_ip": request.httprequest.remote_addr or "",
            "success": request.session.pop(self._flash_key(station), None),
            "page_lang": request.env.lang.split("_")[0] if request.env.lang else "tr",
        }
        response = request.render(
            "ranvals_hr_attendance_qr.mobile_page", values, status=status
        )
        if raw_token and not valid_device:
            self._expire_cookie(response, station)
        return self._response_headers(response)

    @http.route(
        "/ranvals/qr-attendance/<string:token>",
        type="http",
        auth="public",
        methods=["GET"],
        sitemap=False,
    )
    def index(self, token, **kwargs):
        station = self._station(token)
        try:
            self._network(station)
        except (AccessError, UserError) as exc:
            return self._render(station, error=str(exc), denied=True, status=403)
        return self._render(station)

    @http.route(
        "/ranvals/qr-attendance/<string:token>/login",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=True,
        sitemap=False,
        max_content_length=16_384,
    )
    def login(self, token, code="", pin="", remember="", **kwargs):
        self._require_form_content_type()
        station = self._station(token)
        try:
            ip = self._network(station)
        except (AccessError, UserError) as exc:
            return self._render(station, error=str(exc), denied=True, status=403)
        existing_device = self._device(station, touch=False)
        try:
            # Expected authentication failures are deliberately caught outside a
            # savepoint so that the persistent rate-limit counters are committed.
            _device, raw_token = station._authenticate_pin(
                code,
                pin,
                ip,
                remember=remember == "yes",
                replace_device=existing_device,
            )
        except (AccessError, UserError, ValidationError) as exc:
            return self._render(station, error=str(exc), status=403)
        response = request.redirect(self._base_path(station), code=303)
        response.set_cookie(
            self._cookie_name(station),
            raw_token,
            max_age=station.remember_days * 86_400 if remember == "yes" else None,
            path=self._base_path(station),
            secure=True,
            httponly=True,
            samesite="Lax",
        )
        return self._response_headers(response)

    @http.route(
        "/ranvals/qr-attendance/<string:token>/submit",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=True,
        sitemap=False,
        max_content_length=16_384,
    )
    def submit(self, token, intent="", **kwargs):
        self._require_form_content_type()
        station = self._station(token)
        try:
            ip = self._network(station)
        except (AccessError, UserError) as exc:
            return self._render(station, error=str(exc), denied=True, status=403)
        device = self._device(station, touch=False)
        if not device:
            return self._render(
                station,
                error=_("Oturum süreniz doldu. Personel kodu ve PIN ile giriş yapın."),
                status=403,
            )
        try:
            with request.env.cr.savepoint():
                operation = station._perform(device, intent, ip)
        except (AccessError, UserError, ValidationError) as exc:
            return self._render(station, error=str(exc), status=409)
        action = _("İşe girişiniz") if operation.action == "in" else _("İşten çıkışınız")
        request.session[self._flash_key(station)] = _(
            "%(action)s kaydedildi: %(time)s",
            action=action,
            time=station._format_time(operation.event_time),
        )
        return self._response_headers(request.redirect(self._base_path(station), code=303))

    @http.route(
        "/ranvals/qr-attendance/<string:token>/logout",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=True,
        sitemap=False,
        max_content_length=16_384,
    )
    def logout(self, token, **kwargs):
        self._require_form_content_type()
        station = self._station(token)
        # Revocation remains available away from the office; it never records a
        # check-out. HTTPS is still mandatory because a credential is handled.
        if request.httprequest.scheme != "https":
            return self._render(
                station, error=_("HTTPS kullanın."), denied=True, status=403
            )
        device = self._device(station, touch=False)
        if device:
            device.sudo().unlink()
        response = request.redirect(self._base_path(station), code=303)
        self._expire_cookie(response, station)
        return self._response_headers(response)

    @http.route(
        "/ranvals/qr-attendance/print/<int:station_id>",
        type="http",
        auth="user",
        methods=["GET"],
        sitemap=False,
    )
    def print_qr(self, station_id, **kwargs):
        if not request.env.user.has_group(ADMIN):
            raise AccessError(_("Yönetici yetkisi gerekir."))
        # No sudo: regular multi-company access rules remain authoritative.
        station = request.env["ranvals.qr.station"].browse(station_id).exists()
        if not station:
            raise NotFound()
        station.check_access("read")
        image = station.qr_image
        qr_data = "data:image/png;base64," + (
            image.decode() if isinstance(image, bytes) else image
        )
        return self._response_headers(
            request.render(
                "ranvals_hr_attendance_qr.qr_poster",
                {
                    "station": station,
                    "qr_data": qr_data,
                    "page_lang": request.env.lang.split("_")[0]
                    if request.env.lang
                    else "tr",
                },
            )
        )
