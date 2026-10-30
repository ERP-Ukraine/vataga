"""QUNIT adapter for the tested native incremental posting kernel.

Private server API only until the complete Enterprise acceptance scenario has
run. No UI, no administrative unlock flag and no guess based on quantities.
"""
import uuid

from odoo import _, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .native_partial_release import partial_operation
from .quality_native_backorder import _read, _require_roles


class QualityPartialProduction(models.Model):
    _inherit = 'mrp.production'

    def _quality_partial_universe(self):
        self.ensure_one()
        if not self.quality_unit_route_required or self.quality_transfer_controlled:
            raise UserError(_('Часткове продовження доступне лише сучасному поштучному маршруту.'))
        units = self._full_release_units()
        registration = _read(units.registration_run_id)
        if (not units or len(units) != self.product_qty or len(registration) != 1
                or registration.state != 'registered' or registration.quantity != self.product_qty
                or set(registration.unit_ids.ids) != set(units.ids)
                or registration.production_id != self
                or any(u.product_id != self.product_id or u.company_id != self.company_id
                       or u._quality_current_production() != self for u in units)):
            raise ValidationError(_('Потрібна одна підтверджена повна реєстрація QUNIT цього MO.'))
        return units

    def _quality_partial_validate_units(self, units):
        self.ensure_one()
        universe = self._quality_partial_universe()
        if not units or units - universe:
            raise ValidationError(_('Невідомий фізичний склад QUNIT.'))
        units._assert_not_stock_posted()
        workorders = self._full_release_workorders()
        if workorders.time_ids.filtered(lambda t: not t.date_end):
            raise ValidationError(_('Перед випуском закрийте робочі інтервали.'))
        for workorder in workorders:
            history = workorder._quality_native_history().filtered(lambda r: r.state == 'processed')
            if history:
                if units - history.controlled_unit_id:
                    raise ValidationError(_('Не підтверджено виконання %(wo)s саме для вибраних QUNIT.', wo=workorder.display_name))
            elif workorder.state != 'done' or workorder.qty_produced < self.product_qty:
                # Before identity registration, ordinary full native WO
                # completion is evidence for the whole canonical batch. Never
                # backfill fictitious unit executions on these early WOs.
                raise ValidationError(_('Немає підтвердженого виконання %(wo)s.', wo=workorder.display_name))
        self._validate_quality_batch_controls(units, workorders, standard=True)
        self._validate_full_release_lot(units, self.lot_producing_id)
        self.env['quality.stock.posting'].flush_model(['production_id'])
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE production_id=%s LIMIT 1', [self.id])
        if self.env.cr.fetchone():
            raise ValidationError(_('MO має архівне складське проведення; автоматична міграція заборонена.'))
        return True

    def _quality_partial_ready_units(self):
        self.ensure_one()
        units = self._quality_partial_universe()
        ready = self.env['quality.production.unit']
        reasons = {}
        for unit in units:
            if unit._stock_posted():
                continue
            try:
                self._quality_partial_validate_units(unit)
            except AccessError:
                raise
            except UserError as error:
                reasons[unit.id] = str(error)
            else:
                ready |= unit
        return ready, reasons

    def _quality_partial_release(self):
        """Private server entry; intentionally no user-facing button yet."""
        self.ensure_one()
        _require_roles(self.env)
        self.check_access_rights('write')
        self.check_access_rule('write')
        with self.env.cr.savepoint():
            self._native_partial_lock()
            units, reasons = self._quality_partial_ready_units()
            if not units:
                if reasons:
                    raise UserError(_('Немає готових невипущених QUNIT.\n%s') % '\n'.join(sorted(set(reasons.values()))))
                return self.env['quality.native.partial.release']
            return self._native_partial_release(['qunit:%s' % u.id for u in units], str(uuid.uuid4()))

    def _native_partial_before_post(self, operation):
        super()._native_partial_before_post(operation)
        if not self.quality_unit_route_required:
            if self.quality_transfer_controlled:
                raise UserError(_('Історичний маршрут не переводиться у новий частковий випуск.'))
            return
        universe = self._quality_partial_universe()
        selected = universe.filtered(lambda u: 'qunit:%s' % u.id in operation.keys)
        if len(selected) != len(operation.keys):
            raise ValidationError(_('Ключі випуску не відповідають підтвердженим QUNIT.'))
        self._quality_partial_validate_units(selected)
        operation.unit_ids = tuple(selected.ids)

    def _native_partial_after_post(self, release, operation):
        super()._native_partial_after_post(release, operation)
        if not self.quality_unit_route_required:
            return
        units = _read(self.env['quality.production.unit'].browse(operation.unit_ids))
        self._quality_partial_validate_units(units)
        values = []
        for row in release.unit_ids:
            unit = units.filtered(lambda u: row.unit_key == 'qunit:%s' % u.id)
            if len(unit) != 1:
                raise ValidationError(_('Невідома відповідність фізичного QUNIT складській строкі.'))
            values.append({'unit_id': unit.id, 'production_id': self.id,
                           'move_line_id': row.move_line_id.id, 'request_key': 'partial:%s' % release.id})
        facts = self.env['quality.unit.stock.fact'].create(values)
        if set(facts.unit_id.ids) != set(operation.unit_ids):
            raise ValidationError(_('Не всі QUNIT отримали підтверджений складський факт.'))


class QualityPartialWorkorder(models.Model):
    _inherit = 'mrp.workorder'

    def _quality_native_available_units(self, universe):
        available, reasons = super()._quality_native_available_units(universe)
        if self.production_id._native_partial_history():
            available = available.filtered(lambda u: not u._stock_posted())
        return available, reasons

    def _quality_confirm_on_native_finish(self):
        if self.production_id._native_partial_history() and not partial_operation(self.production_id):
            # Native qty_producing is the entered cumulative target, not stock
            # already posted. Restore the full target for aggregate WO closure;
            # the next Stock action still derives its exact one-unit delta.
            self.production_id.write({'qty_producing': self.production_id.product_qty})
        return super()._quality_confirm_on_native_finish()


class QualityPartialAdmission(models.Model):
    _inherit = 'quality.check'

    def _confirm_completed_qc_units(self):
        if not self._identity_production()._native_partial_history():
            return super()._confirm_completed_qc_units()
        self.ensure_one()
        # Closing a hold/retesting the fifth may revisit the original mixed
        # check. Already posted units retain their immutable permission; do
        # not request a NEW admission for them (that public API stays closed).
        for check in self._unit_admission_required_checks().filtered(
                lambda c: c.operation_run_id and c.quality_state in ('pass', 'fail')):
            eligible = check.sample_ids.filtered(lambda s: not s.controlled_unit_id._stock_posted()
                                                 and not check._get_unit_admission_error(s))
            if eligible:
                check._execute_unit_admission_action(eligible.ids, 'confirm', 'qc-completion-%s' % check.id)
