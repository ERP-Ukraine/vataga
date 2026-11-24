from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from ..internal import clean_defaults


class ApprovalProductResolve(models.TransientModel):
    _name = 'approval.product.resolve.vataga'
    _description = 'Заповнення товару погодженої закупочної заявки'

    line_id = fields.Many2one('approval.product.line', required=True, readonly=True)
    description = fields.Char(related='line_id.description', readonly=True, compute_sudo=False)
    product_url = fields.Char(related='line_id.vataga_product_url', readonly=True, compute_sudo=False)
    mode = fields.Selection([('existing', 'Вибрати існуючий товар'),
                             ('new', 'Створити новий товар')], default='existing', required=True)
    product_id = fields.Many2one('product.product', string='Товар',
                                domain="[('purchase_ok', '=', True)]")
    new_product_name = fields.Char(string='Назва нового товару')
    can_create_product = fields.Boolean(compute='_compute_catalog_permissions')
    can_manage_suppliers = fields.Boolean(compute='_compute_catalog_permissions')

    @api.depends_context('uid')
    def _compute_catalog_permissions(self):
        create = (self.env['product.product'].check_access_rights('create', raise_exception=False)
                  and self.env['product.template'].check_access_rights('create', raise_exception=False))
        suppliers = (self.env['product.template'].check_access_rights('write', raise_exception=False)
                     and self.env['product.supplierinfo'].check_access_rights('create', raise_exception=False))
        for wizard in self:
            wizard.can_create_product = create
            wizard.can_manage_suppliers = suppliers

    @api.model
    def default_get(self, names):
        values = super().default_get(names)
        if values.get('line_id'):
            line = self.env['approval.product.line'].browse(values['line_id'])
            line._vataga_assert_can_resolve()
            if 'new_product_name' in names:
                values['new_product_name'] = line.description
        return values

    def _apply(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        line = self.line_id
        line.approval_request_id._vataga_lock()
        line._vataga_assert_can_resolve()
        with self.env.cr.savepoint():
            if self.mode == 'new':
                if not self.can_create_product:
                    raise AccessError(_('Поточні права дозволяють вибрати існуючий товар, але не створити новий.'))
                if not (self.new_product_name or '').strip():
                    raise ValidationError(_('Укажіть назву нового товару.'))
                # Drop caller-supplied product defaults; do not elevate product
                # creation privileges. Standard product ACL/rules still apply.
                context = {k: v for k, v in clean_defaults(self.env.context, set()).items()
                           if not k.startswith('default_')}
                product = self.env['product.product'].with_context(context).create({
                    'name': self.new_product_name.strip(), 'purchase_ok': True,
                    'vataga_purchase_url': line.vataga_product_url,
                    'company_id': line.approval_request_id.company_id.id,
                })
            else:
                product = self.product_id
                if not product:
                    raise ValidationError(_('Оберіть товар.'))
            line._vataga_resolve_product(product, created=self.mode == 'new')
            return product

    def action_apply(self):
        self._apply()
        return {'type': 'ir.actions.act_window_close'}

    def action_apply_open_product(self):
        product = self._apply()
        return {'type': 'ir.actions.act_window', 'name': _('Товар та постачальники'),
                'res_model': 'product.product', 'res_id': product.id,
                'view_mode': 'form', 'target': 'current'}
