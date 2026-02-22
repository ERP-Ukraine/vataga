from odoo import models, fields, api, _
from string import Template

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    template_purpose_dcu = fields.Char(
        string="PUMB Purpose Template",
        config_parameter="account_vataga.template_purpose_dcu"
    )
    preview_text = fields.Char(string="Preview", compute="_compute_preview_text")

    @api.depends('template_purpose_dcu')
    def _compute_preview_text(self):
        test_context = {
            "ref": "TEST_REF_001",
            "invoice_date": "14.08.2025",
            "sale_ua_contract_id": "DOG-2025/15",
            "sc_date_start": "01.01.2025",
            "ua_contract_id": "DOG-2024/17",
            "uc_date_start": "01.01.2024",
            "tax_info": _('inc. VAT 20% 1815,00')
        }
        for record in self:
            if not record.template_purpose_dcu:
                record.preview_text = ""
                continue
            record.preview_text = Template(record.template_purpose_dcu).safe_substitute(test_context)
