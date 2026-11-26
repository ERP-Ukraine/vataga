from odoo import fields, models, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    vataga_default_supplier_id = fields.Many2one(
        'res.partner', string='Довільний постачальник для нових товарів Vataga',
        domain="[('active', '=', True), ('supplier_rank', '>', 0), ('company_id', 'in', [False, id])]",
        check_company=True, help='Існуючий контрагент «довільний постачальник». Лише для майстра закупочної заявки.')

    def _vataga_default_supplier(self):
        self.ensure_one()
        partner = self.vataga_default_supplier_id
        if not partner:
            raise ValidationError(_('Налаштуйте для компанії поле «Довільний постачальник для нових товарів Vataga»: виберіть існуючого контрагента «довільний постачальник».'))
        partner.check_access_rights('read')
        partner.check_access_rule('read')
        if (not partner.active or partner.supplier_rank <= 0
                or partner.company_id and partner.company_id != self
                or partner.name.strip().casefold() != 'довільний постачальник'):
            raise ValidationError(_('Налаштований «довільний постачальник» має бути активним постачальником, доступним компанії заявки.'))
        return partner
