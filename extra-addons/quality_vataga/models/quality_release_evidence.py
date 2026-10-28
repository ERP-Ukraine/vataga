"""Historical extension registrations, protected certificate fields and QC label.

The former approval/revision hooks are retired; these schemas stay registered
until database inventory permits removing historical document dependencies.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


def release_quantity_dependencies(model):
    dependencies = ['production_id', 'production_id.product_qty', 'production_id.product_uom_id']
    if 'workorder_id' in model._fields:
        dependencies += ['workorder_id', 'workorder_id.production_id.product_qty', 'workorder_id.production_id.product_uom_id']
    return dependencies


class QualityReleaseEvidenceUnit(models.Model):
    _inherit = 'quality.production.unit'


class QualityReleaseEvidenceCheck(models.Model):
    _inherit = 'quality.check'

    quality_release_mo_quantity_label = fields.Char(string='Кількість товару у виробничому замовленні', compute='_compute_release_mo_quantity_label')

    @api.depends(release_quantity_dependencies)
    def _compute_release_mo_quantity_label(self):
        for check in self:
            production = check._identity_production()
            check.quality_release_mo_quantity_label = check._format_operation_product_quantity(production.product_qty, production.product_uom_id) if production else False


class QualityReleaseEvidenceAdmission(models.Model):
    _inherit = 'quality.unit.admission'


class QualityReleaseEvidenceAlert(models.Model):
    _inherit = 'quality.alert'

    quality_release_scope_unit_id = fields.Many2one('quality.production.unit', string='QUNIT рішення Технічної ради', readonly=True, copy=False, ondelete='restrict')
    quality_release_scope_check_id = fields.Many2one('quality.check', readonly=True, copy=False, ondelete='restrict')


    @api.model_create_multi
    def create(self, vals_list):
        protected = {'quality_release_scope_unit_id', 'quality_release_scope_check_id'}
        if any(protected.intersection(vals) for vals in vals_list):
            raise UserError(_('Поіменну сферу затвердженого рішення фіксує система.'))
        context = {key: value for key, value in self.env.context.items() if key not in {'default_' + name for name in protected}}
        return super(QualityReleaseEvidenceAlert, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if {'quality_release_scope_unit_id', 'quality_release_scope_check_id'}.intersection(vals):
            raise UserError(_('Поіменна сфера затвердженого рішення незмінна.'))
        return super().write(vals)


class QualityReleaseEvidencePoint(models.Model):
    _inherit = 'quality.point'
