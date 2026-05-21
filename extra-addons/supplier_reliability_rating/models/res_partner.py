from markupsafe import Markup

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    supplier_reliability_rating = fields.Selection(
        selection=[
            ("trial", "Випробування"),
            ("approved", "Затверджено"),
            ("blocked", "Заблоковано"),
        ],
        string="Рейтинг надійності",
        default="trial",
        required=True,
    )
    supplier_reliability_badge = fields.Html(
        string="Рейтинг надійності",
        compute="_compute_supplier_reliability_badge",
        sanitize=False,
    )

    def _get_supplier_reliability_rating_data(self):
        self.ensure_one()
        return {
            "trial": {
                "label": "Випробування",
                "class": "o_supplier_reliability_trial",
                "marker": "🟠",
            },
            "approved": {
                "label": "Затверджено",
                "class": "o_supplier_reliability_approved",
                "marker": "🟢",
            },
            "blocked": {
                "label": "Заблоковано",
                "class": "o_supplier_reliability_blocked",
                "marker": "🔴",
            },
        }[self.supplier_reliability_rating or "trial"]

    @api.depends("supplier_reliability_rating")
    def _compute_supplier_reliability_badge(self):
        for partner in self:
            rating_data = partner._get_supplier_reliability_rating_data()
            partner.supplier_reliability_badge = Markup(
                '<span class="o_supplier_reliability_badge %(class)s">'
                '<i class="fa fa-flag" aria-label="%(label)s"></i>'
                '<span>%(label)s</span>'
                "</span>"
            ) % rating_data

    def _show_supplier_reliability_marker(self):
        self.ensure_one()
        if "supplier_rank" not in self._fields:
            return True
        return self.supplier_rank > 0

    @api.depends("supplier_reliability_rating")
    @api.depends_context(
        "show_address",
        "show_vat",
        "show_email",
        "show_phone",
        "show_mobile",
        "show_address_only",
        "show_name",
    )
    def _compute_display_name(self):
        super()._compute_display_name()
        for partner in self:
            if partner._show_supplier_reliability_marker():
                rating_data = partner._get_supplier_reliability_rating_data()
                partner.display_name = f"{rating_data['marker']} {partner.display_name}"
