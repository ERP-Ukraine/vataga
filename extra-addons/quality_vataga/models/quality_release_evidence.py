from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def release_quantity_dependencies(model):
    dependencies = ['production_id', 'production_id.product_qty', 'production_id.product_uom_id']
    if 'workorder_id' in model._fields:
        dependencies += ['workorder_id', 'workorder_id.production_id.product_qty', 'workorder_id.production_id.product_uom_id']
    return dependencies


def invalidate_release_views(env):
    env['quality.production.unit'].invalidate_model([
        'quality_release_state', 'quality_release_reason', 'quality_release_last_workorder_id',
        'quality_release_execution_done', 'quality_release_quality_result', 'quality_release_admission_id',
    ])
    env['quality.unit.release.line'].invalidate_model(['is_effective', 'ineffective_reason', 'current_state'])
    env['mrp.production'].invalidate_model([
        'quality_release_total', 'quality_release_ready', 'quality_release_authorized',
        'quality_release_not_ready', 'quality_release_blocked', 'quality_release_produced',
    ])


class QualityReleaseEvidenceUnit(models.Model):
    _inherit = 'quality.production.unit'

    def _bump_release_revision(self):
        if not self:
            return
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.production_id.sorted('id')._lock_quality_transfer_scope()
        self.flush_recordset(['quality_release_revision'])
        self.env.cr.execute('UPDATE quality_production_unit SET quality_release_revision = COALESCE(quality_release_revision, 0) + 1 WHERE id IN %s', [tuple(sorted(self.ids))])
        self.invalidate_recordset(['quality_release_revision'], flush=False)
        self.modified(['quality_release_revision'])
        invalidate_release_views(self.env)


class QualityReleaseEvidenceCheck(models.Model):
    _inherit = 'quality.check'

    quality_release_mo_quantity_label = fields.Char(string='Кількість товару у виробничому замовленні', compute='_compute_release_mo_quantity_label')

    @api.depends(release_quantity_dependencies)
    def _compute_release_mo_quantity_label(self):
        for check in self:
            production = check._identity_production()
            check.quality_release_mo_quantity_label = check._format_operation_product_quantity(production.product_qty, production.product_uom_id) if production else False

    def _release_scope_units(self):
        units = self.env['quality.production.unit']
        for check in self.filtered(lambda row: row.x_control_stage in ('in_process', 'final') and row.unit_admission_role == 'required'):
            if check.quality_execution_ids:
                units |= check.quality_execution_ids.controlled_unit_id
            elif check.operation_run_id:
                units |= check.operation_run_id.unit_ids
            else:
                # An unidentified required QC cannot be assumed to target a
                # different unit. Only registered units of this exact MO.
                units |= self.env['quality.production.unit'].search([('production_id', '=', check._identity_production().id)])
        return units

    @api.model_create_multi
    def create(self, vals_list):
        checks = super().create(vals_list)
        checks._release_scope_units()._bump_release_revision()
        invalidate_release_views(self.env)
        return checks

    def write(self, vals):
        relevant = {'unit_admission_role', 'point_id', 'operation_run_id', 'quality_execution_ids', 'production_id', 'workorder_id'}
        units = self._release_scope_units() if relevant.intersection(vals) else self.env['quality.production.unit']
        # Existing admission/evidence methods also acquire MO before QRUN.
        if units:
            units.production_id.sorted('id')._lock_quality_transfer_scope()
        result = super().write(vals)
        if relevant.intersection(vals):
            (units | self._release_scope_units())._bump_release_revision()
        invalidate_release_views(self.env)
        return result

    def unlink(self):
        units = self._release_scope_units()
        units._bump_release_revision()
        result = super().unlink()
        invalidate_release_views(self.env)
        return result


class QualityReleaseEvidenceAdmission(models.Model):
    _inherit = 'quality.unit.admission'

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        invalidate_release_views(self.env)
        return records

    def write(self, vals):
        result = super().write(vals)
        invalidate_release_views(self.env)
        return result


class QualityReleaseEvidenceAlert(models.Model):
    _inherit = 'quality.alert'

    quality_release_scope_unit_id = fields.Many2one('quality.production.unit', string='QUNIT рішення Технічної ради', readonly=True, copy=False, ondelete='restrict')
    quality_release_scope_check_id = fields.Many2one('quality.check', readonly=True, copy=False, ondelete='restrict')

    def _release_alert_productions(self):
        productions = self.env['mrp.production']
        if 'check_id' in self._fields:
            for check in self.check_id:
                productions |= check._identity_production()
        for name, model in [('production_id', 'mrp.production'), ('workorder_id', 'mrp.workorder')]:
            field = self._fields.get(name)
            if field and getattr(field, 'comodel_name', None) == model:
                productions |= self[name] if model == 'mrp.production' else self[name].production_id
        return productions

    def _lock_board_records(self):
        # The standard board guard locks the alert row. Always take related
        # MOs first, matching release/admission/execution lock order.
        self._release_lock_alert_scope()
        return super()._lock_board_records()

    def _release_lock_alert_scope(self):
        try:
            self._release_alert_productions().sorted('id')._lock_quality_transfer_scope()
        except AccessError:
            # Board approval retains its original ACLs. Lack of access to
            # manufacturing evidence never grants a release certificate;
            # readiness independently fails closed on that evidence.
            return False
        return True

    @api.model_create_multi
    def create(self, vals_list):
        protected = {'quality_release_scope_unit_id', 'quality_release_scope_check_id'}
        if any(protected.intersection(vals) for vals in vals_list):
            raise UserError(_('Поіменну сферу затвердженого рішення фіксує система.'))
        context = {key: value for key, value in self.env.context.items() if key not in {'default_' + name for name in protected}}
        alerts = super(QualityReleaseEvidenceAlert, self.with_context(context)).create(vals_list)
        alerts._release_lock_alert_scope()
        invalidate_release_views(self.env)
        return alerts

    def write(self, vals):
        if {'quality_release_scope_unit_id', 'quality_release_scope_check_id'}.intersection(vals):
            raise UserError(_('Поіменна сфера затвердженого рішення незмінна.'))
        self._release_lock_alert_scope()
        result = super().write(vals)
        invalidate_release_views(self.env)
        return result

    def action_approve_technical_board(self):
        self.ensure_one()
        was_approved = self.is_approved
        result = super().action_approve_technical_board()
        # Only a newly authorized board action can certify its exact scope.
        # Historical approvals and multi-unit text snapshots stay ambiguous.
        check = self.check_id
        try:
            units = check.sample_ids.controlled_unit_id if len(check.sample_ids) == 1 else self.env['quality.production.unit']
            units.check_access_rights('read')
            units.check_access_rule('read')
        except AccessError:
            units = self.env['quality.production.unit']
        if not was_approved and self.is_approved and not self.quality_release_scope_unit_id and len(units) == 1:
            super(QualityReleaseEvidenceAlert, self).write({
                'quality_release_scope_unit_id': units.id,
                'quality_release_scope_check_id': check.id,
            })
        invalidate_release_views(self.env)
        return result

    def unlink(self):
        self._release_lock_alert_scope()
        result = super().unlink()
        invalidate_release_views(self.env)
        return result


class QualityReleaseEvidencePoint(models.Model):
    _inherit = 'quality.point'

    @api.model_create_multi
    def create(self, vals_list):
        points = super().create(vals_list)
        invalidate_release_views(self.env)
        return points

    def write(self, vals):
        result = super().write(vals)
        invalidate_release_views(self.env)
        return result

    def unlink(self):
        result = super().unlink()
        invalidate_release_views(self.env)
        return result
