"""Named continuation guards; no stock mutation and no costing ACL grants."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .stock_posting_core import access, production_sources, move_sources


class QualityStockHistory(models.Model):
    _inherit = 'mrp.production'

    def _check_named_stock_history(self):
        self.ensure_one()
        access(self)
        self.env.flush_all()
        self.env.cr.execute("SELECT EXISTS(SELECT 1 FROM quality_stock_posting WHERE production_id=%s) OR EXISTS(SELECT 1 FROM stock_move WHERE (production_id=%s OR raw_material_production_id=%s) AND state='done')", [self.id, self.id, self.id])
        if not self.env.cr.fetchone()[0]:
            return self.env['quality.production.unit']
        moves = production_sources(self, 'stock.move', 'production_id = %s OR raw_material_production_id = %s')
        move_sources(moves, 'stock.move.line', 'move_id')
        self.env.cr.execute('SELECT unit_id FROM quality_stock_posting WHERE production_id=%s ORDER BY id', [self.id])
        ids = [row[0] for row in self.env.cr.fetchall()]
        units = self.env['quality.production.unit'].browse(ids)
        access(units)
        done = moves.filtered(lambda move: move.production_id == self and move.state == 'done')
        if (any(not item or item.production_id != self or item.company_id != self.company_id for item in units)
                or len(ids) != len(set(ids)) or self.qty_produced != len(ids) or len(done) != len(ids)):
            raise ValidationError(_('Складська історія MO не відповідає справжньому поіменному реєстру.'))
        # Protective Boolean predicates only. Never return protected valuation
        # values/snapshots to an inspector without the native valuation ACL.
        # Actual posting still performs full ordinary ACL checks on every SVL.
        self.env.cr.execute('''
            SELECT EXISTS (
                SELECT 1 FROM quality_stock_posting p
                JOIN stock_move m ON m.id=p.finished_move_id
                JOIN stock_move_line f ON f.id=p.finished_line_id
                JOIN quality_unit_release_line r ON r.id=p.permission_id
                WHERE p.production_id=%s AND (
                    m.state!='done' OR f.move_id!=m.id OR f.quantity!=1
                    OR m.production_id!=p.production_id OR m.product_id!=%s OR m.company_id!=%s
                    OR r.unit_id!=p.unit_id OR r.production_id!=p.production_id
                    OR ABS(p.component_cost + COALESCE((
                        SELECT SUM(v.value) FROM stock_valuation_layer v WHERE v.id IN (
                            SELECT l.valuation_layer_id FROM quality_stock_posting_line l
                            WHERE l.posting_id=p.id AND l.kind='component')), 0)) >= %s
                    OR ABS(CASE WHEN p.valuation_snapshot->>'profile'='standard_real_time_v1'
                        THEN (p.valuation_snapshot->>'standard_price')::numeric
                        ELSE p.component_cost+p.labour_cost END-COALESCE((
                        SELECT SUM(v.value) FROM stock_valuation_layer v WHERE v.stock_move_id=m.id), 0)) >= %s
                ))
        ''', [self.id, self.product_id.id, self.company_id.id,
              self.company_id.currency_id.rounding / 2, self.company_id.currency_id.rounding / 2])
        if self.env.cr.fetchone()[0]:
            raise ValidationError(_('Проведені кількості, дозволи або оцінка не відповідають реєстру.'))
        self.env.cr.execute('''
            WITH expected AS (
                SELECT p.finished_line_id AS consume_line_id, l.move_line_id AS produce_line_id
                FROM quality_stock_posting p JOIN quality_stock_posting_line l ON l.posting_id=p.id
                WHERE p.production_id=%s AND l.kind='component'
            ), actual AS (
                SELECT rel.consume_line_id, rel.produce_line_id FROM stock_move_line_consume_rel rel
                WHERE rel.consume_line_id IN (SELECT id FROM stock_move_line WHERE move_id IN (
                    SELECT id FROM stock_move WHERE production_id=%s AND state='done'))
                OR rel.produce_line_id IN (SELECT id FROM stock_move_line WHERE move_id IN (
                    SELECT id FROM stock_move WHERE raw_material_production_id=%s AND state='done'))
            )
            SELECT EXISTS ((SELECT * FROM expected EXCEPT SELECT * FROM actual)
                           UNION (SELECT * FROM actual EXCEPT SELECT * FROM expected))
        ''', [self.id, self.id, self.id])
        if self.env.cr.fetchone()[0]:
            raise ValidationError(_('Нативні consume/produce зв’язки не відповідають поіменному реєстру.'))
        raw_done = moves.filtered(lambda move: move.raw_material_production_id == self and move.state == 'done')
        self.env.cr.execute("SELECT l.move_line_id FROM quality_stock_posting_line l JOIN quality_stock_posting p ON p.id=l.posting_id WHERE p.production_id=%s AND l.kind='component'", [self.id])
        if set(raw_done.move_line_ids.ids) != {row[0] for row in self.env.cr.fetchall()}:
            raise ValidationError(_('Є невідоме або незареєстроване споживання компонентів.'))
        return units


class QualityPostedUnit(models.Model):
    _inherit = 'quality.production.unit'

    def _stock_posted(self):
        access(self)
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE unit_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone())

    def _assert_not_stock_posted(self):
        if self._stock_posted():
            raise UserError(_('Виріб уже випущено на склад. Повторний маршрут, QC або обробка потребують окремої процедури повернення.'))


class QualityPostedAdmission(models.Model):
    _inherit = 'quality.unit.admission'

    def _get_consumption_error(self):
        if self.controlled_unit_id._stock_posted():
            return _('Допуск використано фактичним складським випуском. Звичайне скасування заборонено.')
        return super()._get_consumption_error()


class QualityPostedPermission(models.Model):
    _inherit = 'quality.unit.release.line'

    def _stock_permission_used(self):
        access(self)
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE permission_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        return bool(self.env.cr.fetchone())


class QualityPostedTransfer(models.Model):
    _inherit = 'quality.unit.transfer'

    @api.model
    def _check_admission(self, admission, destination):
        admission.controlled_unit_id._assert_not_stock_posted()
        return super()._check_admission(admission, destination)

    def _get_execution_error(self, workorder, current=None):
        if self.controlled_unit_id._stock_posted():
            return _('Виріб уже випущено на склад.')
        return super()._get_execution_error(workorder, current=current)


class QualityPostedExecution(models.Model):
    _inherit = 'quality.unit.execution'

    def _validate_control_selection(self, workorder):
        self.controlled_unit_id._assert_not_stock_posted()
        return super()._validate_control_selection(workorder)


class QualityPostedCheck(models.Model):
    _inherit = 'quality.check'

    def _get_unit_admission_error(self, sample):
        if sample.controlled_unit_id._stock_posted():
            return _('Виріб уже випущено на склад; повторний допуск заборонено.')
        return super()._get_unit_admission_error(sample)

    def _identify_samples_locked(self, mode, mapping, run, workorder, confirmed):
        units = self.env['quality.production.unit'].browse([row['unit_id'] for row in mapping if row.get('unit_id')])
        units._assert_not_stock_posted()
        return super()._identify_samples_locked(mode, mapping, run, workorder, confirmed)


class QualityPostedSample(models.Model):
    _inherit = 'quality.check.sample'

    def _prepare_rework_tracking(self):
        self.controlled_unit_id._assert_not_stock_posted()
        return super()._prepare_rework_tracking()
