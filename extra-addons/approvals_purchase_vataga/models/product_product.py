from odoo import models, _
from odoo.exceptions import AccessError, ValidationError


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def web_save(self, vals, specification, next_id=None):
        wizard_id = self.env.context.get('vataga_product_editor_id')
        if not wizard_id:
            return super().web_save(vals, specification, next_id=next_id)
        if not isinstance(wizard_id, int) or isinstance(wizard_id, bool):
            raise AccessError(_('Недійсний майстер створення товару.'))
        wizard = self.env['approval.product.resolve.vataga'].browse(wizard_id).exists()
        if not wizard:
            raise AccessError(_('Майстер створення товару вже недоступний.'))
        line = wizard._vataga_editor_line()
        line.approval_request_id._vataga_lock()
        line._vataga_assert_can_resolve()
        if next_id or len(self) > 1 or (wizard.mode == 'new' and self) or (
                wizard.mode == 'existing' and self != wizard.product_id):
            raise AccessError(_('Товар не відповідає поточному майстру.'))
        # Standard form values (including edited UOM and seller commands) are
        # saved through the complete native MRO in this one transaction.
        context = dict(self.env.context)
        context.pop('vataga_product_editor_id', None)
        with self.env.cr.savepoint():
            result = super(ProductProduct, self.with_context(context)).web_save(
                vals, specification, next_id=None)
            if len(result) != 1 or not result[0].get('id'):
                raise ValidationError(_('Не вдалося зберегти єдиний товар.'))
            product = self.browse(result[0]['id'])
            line._vataga_resolve_product(product, created=wizard.mode == 'new')
            return result
