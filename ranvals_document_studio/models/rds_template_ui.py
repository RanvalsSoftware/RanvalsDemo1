from markupsafe import Markup

from odoo import api, fields, models, _


STYLE_PRESETS = {
    "beauty": {
        "primary_color": "#7A1020",
        "secondary_color": "#FFF9F5",
        "accent_color": "#B97865",
        "text_color": "#332B2B",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "construction": {
        "primary_color": "#0B2A50",
        "secondary_color": "#F5F7F9",
        "accent_color": "#F36B21",
        "text_color": "#24313D",
        "heading_font": "technical",
        "body_font": "sans",
    },
    "technology": {
        "primary_color": "#155BD7",
        "secondary_color": "#F4F8FF",
        "accent_color": "#10B9E8",
        "text_color": "#17263B",
        "heading_font": "sans",
        "body_font": "sans",
    },
    "industrial": {
        "primary_color": "#123865",
        "secondary_color": "#F3F6F9",
        "accent_color": "#6C8FB8",
        "text_color": "#1E2E3E",
        "heading_font": "technical",
        "body_font": "sans",
    },
    "eco": {
        "primary_color": "#245D46",
        "secondary_color": "#F2F8F4",
        "accent_color": "#8FAE77",
        "text_color": "#25352E",
        "heading_font": "serif",
        "body_font": "sans",
    },
    "furniture": {
        "primary_color": "#6B4226",
        "secondary_color": "#FBF7F2",
        "accent_color": "#B9845A",
        "text_color": "#352A24",
        "heading_font": "editorial",
        "body_font": "sans",
    },
}


class RdsTemplate(models.Model):
    _inherit = "rds.template"

    design_preview_html = fields.Html(
        compute="_compute_design_preview_html",
        sanitize=False,
        string="Canlı Tasarım Önizlemesi",
    )

    @api.onchange("layout_style")
    def _onchange_layout_style_design(self):
        """Apply the selected sector preset immediately in the form editor."""
        for record in self:
            preset = STYLE_PRESETS.get(record.layout_style)
            if preset:
                record.update(dict(preset))

    def action_apply_style_preset(self):
        """Re-apply the current style's official palette and typography."""
        self.ensure_one()
        preset = STYLE_PRESETS.get(self.layout_style)
        if preset:
            self.write(dict(preset))
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_apply_design(self):
        """The form controller saves dirty values before object buttons run."""
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Tasarım kaydedildi"),
                "message": _("Renk, yazı tipi ve metin ayarlarınız belge çıktılarında kullanılacak."),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    @api.depends(
        "layout_style",
        "primary_color",
        "secondary_color",
        "accent_color",
        "text_color",
        "heading_font",
        "body_font",
        "tagline",
        "footer_text",
    )
    def _compute_design_preview_html(self):
        for record in self:
            theme = record.get_theme()
            # Markup.format escapes every dynamic placeholder.  CSS-bearing
            # values additionally come from normalized colors or fixed font
            # selections in get_theme(), so this sanitize=False computed field
            # cannot turn administrator-entered text into executable markup.
            record.design_preview_html = Markup(
                """
                <div class="rds-live-document" style="font-family:{body_font};color:{text};background:#fff;border:1px solid #dfe7f2;border-radius:22px;overflow:hidden;box-shadow:0 18px 52px rgba(15,39,71,.10);">
                    <div style="height:10px;background:linear-gradient(90deg,{primary},{accent});"></div>
                    <div style="padding:22px 24px 18px;background:linear-gradient(135deg,{secondary} 0%,#ffffff 74%);border-bottom:1px solid #e8edf4;">
                        <table style="width:100%;border-collapse:collapse;"><tr>
                            <td style="width:62%;vertical-align:top;">
                                <div style="font-size:10px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:{accent};">{style_label}</div>
                                <div style="margin-top:7px;font-family:{heading_font};font-size:28px;line-height:1.08;font-weight:800;color:{primary};">TEKLİF / PROFORMA</div>
                                <div style="margin-top:7px;font-size:13px;font-weight:700;color:{text};">No: DC-2026-00125</div>
                            </td>
                            <td style="width:38%;vertical-align:top;text-align:right;">
                                <div style="display:inline-block;padding:9px 13px;border-radius:12px;background:{primary};color:#fff;font-size:12px;font-weight:800;">DOCUCRAFT</div>
                                <div style="margin-top:9px;font-size:11px;line-height:1.45;color:{text};">{tagline}</div>
                            </td>
                        </tr></table>
                    </div>
                    <div style="padding:18px 24px 22px;">
                        <table style="width:100%;border-collapse:separate;border-spacing:10px 0;margin:0 -10px 16px;">
                            <tr>
                                <td style="width:50%;padding:14px;border:1px solid #e4eaf2;border-radius:14px;background:{secondary};">
                                    <div style="font-size:11px;font-weight:800;color:{primary};">FİRMA BİLGİLERİ</div>
                                    <div style="margin-top:8px;font-size:10px;line-height:1.55;">DocuCraft Demo A.Ş.<br/>İstanbul / Türkiye<br/>info@example.com</div>
                                </td>
                                <td style="width:50%;padding:14px;border:1px solid #e4eaf2;border-radius:14px;">
                                    <div style="font-size:11px;font-weight:800;color:{primary};">MÜŞTERİ BİLGİLERİ</div>
                                    <div style="margin-top:8px;font-size:10px;line-height:1.55;">Örnek Müşteri Ltd.<br/>Ankara / Türkiye<br/>VKN: 1234567890</div>
                                </td>
                            </tr>
                        </table>
                        <table style="width:100%;border-collapse:collapse;border:1px solid #e1e7ef;border-radius:12px;overflow:hidden;">
                            <thead><tr style="background:{primary};color:#fff;">
                                <th style="padding:9px;text-align:left;font-size:9px;">AÇIKLAMA</th>
                                <th style="padding:9px;text-align:center;font-size:9px;">MİKTAR</th>
                                <th style="padding:9px;text-align:right;font-size:9px;">BİRİM FİYAT</th>
                                <th style="padding:9px;text-align:right;font-size:9px;">TUTAR</th>
                            </tr></thead>
                            <tbody>
                                <tr><td style="padding:9px;border-bottom:1px solid #e7ebf0;font-size:10px;">Kurumsal Belge Tasarımı</td><td style="padding:9px;text-align:center;border-bottom:1px solid #e7ebf0;font-size:10px;">1</td><td style="padding:9px;text-align:right;border-bottom:1px solid #e7ebf0;font-size:10px;">12.500,00 TL</td><td style="padding:9px;text-align:right;border-bottom:1px solid #e7ebf0;font-size:10px;">12.500,00 TL</td></tr>
                                <tr style="background:{secondary};"><td style="padding:9px;font-size:10px;">PDF / DOCX / PNG Çıktısı</td><td style="padding:9px;text-align:center;font-size:10px;">1</td><td style="padding:9px;text-align:right;font-size:10px;">2.500,00 TL</td><td style="padding:9px;text-align:right;font-size:10px;">2.500,00 TL</td></tr>
                            </tbody>
                        </table>
                        <div style="width:48%;margin:18px 0 0 auto;">
                            <div style="display:flex;justify-content:space-between;padding:7px 10px;font-size:10px;"><span>Ara Toplam</span><strong>15.000,00 TL</strong></div>
                            <div style="display:flex;justify-content:space-between;padding:10px;border-radius:10px;background:{primary};color:#fff;font-size:12px;"><strong>GENEL TOPLAM</strong><strong>18.000,00 TL</strong></div>
                        </div>
                        <div style="margin-top:17px;padding-top:12px;border-top:1px solid #e6ebf2;font-size:10px;color:{text};">{footer}</div>
                    </div>
                </div>
                """
            ).format(
                primary=theme["primary"],
                secondary=theme["secondary"],
                accent=theme["accent"],
                text=theme["text"],
                heading_font=theme["heading_font"],
                body_font=theme["body_font"],
                tagline=record.tagline or _("Modern işletmeler için akıllı belgeler"),
                footer=record.footer_text
                or _("Kurumsal belge tasarımınızı anında özelleştirin."),
                style_label=dict(record._fields["layout_style"].selection).get(
                    record.layout_style, ""
                ),
            )
