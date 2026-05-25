from markupsafe import Markup

from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    supplier_reliability_rating_id = fields.Many2one(
        comodel_name="supplier.reliability.rating",
        string="Рейтинг надійності",
        default=lambda self: self._default_supplier_reliability_rating_id(),
        ondelete="restrict",
    )
    supplier_reliability_badge = fields.Html(
        string="Рейтинг надійності",
        compute="_compute_supplier_reliability_badge",
        sanitize=False,
    )

    def _default_supplier_reliability_rating_id(self):
        return self.env.ref(
            "supplier_reliability_rating.supplier_reliability_rating_trial",
            raise_if_not_found=False,
        )

    def _get_supplier_reliability_rating_data(self):
        self.ensure_one()
        rating = (
            self.supplier_reliability_rating_id
            or self._default_supplier_reliability_rating_id()
        )
        if not rating:
            return {
                "label": "Випробування",
                "class": "o_supplier_reliability_trial",
                "marker": "🟠",
            }
        return {
            "label": rating.name or "",
            "class": rating.css_class or "o_supplier_reliability_trial",
            "marker": rating.marker or "🟠",
        }

    @api.depends("supplier_reliability_rating_id")
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

    @api.depends("supplier_reliability_rating_id")
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
