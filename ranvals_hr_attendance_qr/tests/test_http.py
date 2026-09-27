"""Public-route smoke tests, including real QWeb and asset rendering."""

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "ranvals_qr")
class TestRanvalsQRHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.station = cls.env["ranvals.qr.station"].create(
            {
                "name": "HTTP TEST — NOT LIVE",
                "brand_name": "Ranvals HTTP Test",
                "base_url": "https://http.example.test",
                "allowed_networks": "8.8.8.8/32",
                "enabled": True,
            }
        )

    def test_http_is_denied_with_rendered_safe_page(self):
        response = self.url_open(
            f"/ranvals/qr-attendance/{self.station.sudo().token}",
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Ranvals HTTP Test", response.text)
        self.assertIn("HTTPS", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
        self.assertNotIn("Shape Store", response.text)
