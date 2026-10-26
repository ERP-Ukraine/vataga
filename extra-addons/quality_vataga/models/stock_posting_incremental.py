"""Append-only cost scopes; the named population/BOM stay fixed, time grows."""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .stock_cost_allocator import allocate_intervals
from .stock_posting_core import CORE_CONTEXT, CORE_TOKEN, access, production_sources


INCREMENTAL_POLICY = 'equal_closed_members_v2'


class StockIncrementalCostScope(models.Model):
    _inherit = 'quality.stock.cost.scope'

    @api.model
    def _all_scopes(self, production):
        return production_sources(production, self._name, 'production_id = %s')

    @api.model
    def _fixed_data(self, production, keys, scopes):
        if scopes:
            data = scopes[0].snapshot
            if scopes.filtered(lambda row: row.policy != INCREMENTAL_POLICY):
                raise ValidationError(_('Історична політика v1 не підтримує продовження часу. Потрібна окрема міграція вартості.'))
            if sorted(keys) != data['keys']:
                raise ValidationError(_('Зафіксований поіменний склад MO змінився.'))
            return {name: data[name] for name in ('keys', 'quantity', 'product', 'bom', 'uom', 'company', 'currency', 'rounding', 'components')}
        expected = production._get_moves_raw_values()
        from .stock_release_plan_utils import proportional_quantity
        components = [dict(bom_line=row['bom_line_id'], product=row['product_id'], uom=row['product_uom'],
                           total=row['product_uom_qty'], per_unit=proportional_quantity(
                               row['product_uom_qty'], 1, len(keys), self.env['uom.uom'].browse(row['product_uom']).rounding)) for row in expected]
        if not components or len({row['bom_line'] for row in components}) != len(components):
            raise ValidationError(_('Потрібна однозначна BOM для всього поіменного складу.'))
        return dict(keys=sorted(keys), quantity=production.product_qty, product=production.product_id.id,
                    bom=production.bom_id.id, uom=production.product_uom_id.id, company=production.company_id.id,
                    currency=production.company_id.currency_id.id, rounding=production.company_id.currency_id.rounding,
                    components=components)

    @api.model
    def _incremental_facts(self, production, keys, members, selected, scopes):
        timers = self._timers(production)
        if any(timer.company_id != production.company_id or timer.workorder_id.company_id != production.company_id for timer in timers):
            raise ValidationError(_('Робочі інтервали мають належати компанії цього MO.'))
        facts = self._timer_facts(timers, members)
        if any(not row['members'] or len(set(row['members'])) != len(row['members'])
               or not set(row['members']) <= set(keys) for row in facts):
            raise ValidationError(_('Кожен інтервал потребує однозначного складу саме цього MO.'))
        previous = {}
        for scope in scopes:
            for row in scope.snapshot['timers']:
                if row['id'] in previous and row != previous[row['id']]:
                    raise ValidationError(_('Історія розподілу одного інтервалу суперечлива.'))
                previous[row['id']] = row
        issued = set(self.env['quality.stock.posting']._receipts(production).mapped('unit_key'))
        for row in facts:
            # A later work-center rate is prospective. Closed historical
            # intervals retain their audited rate, never a hidden revaluation.
            if row['id'] in previous:
                row['rate'] = previous[row['id']]['rate']
            if row['id'] in previous and row != previous[row['id']]:
                raise ValidationError(_('Раніше зафіксований інтервал змінено. Переоцінку випущених виробів заборонено.'))
            if set(row['members']) & issued and row['id'] not in previous:
                raise ValidationError(_('Новий час віднесено до вже випущеного виробу. Потрібна окрема процедура коригування.'))
        relevant = [row for row in facts if set(row['members']) & set(selected)]
        workorders = production_sources(production, 'mrp.workorder', 'production_id = %s')
        for key in selected:
            if set(workorders.ids) != {row['workorder'] for row in relevant if key in row['members']}:
                raise ValidationError(_('Для вибраного виробу немає явного часу всіх операцій. Нульова вартість не припускається.'))
        for row in relevant:
            if not row['end']:
                raise ValidationError(_('Відкритий інтервал вибраного виробу блокує тільки його випуск.'))
            for other in facts:
                if (other['id'] != row['id'] and other['workorder'] == row['workorder']
                        and other['start'] < row['end'] and (not other['end'] or other['end'] > row['start'])):
                    raise ValidationError(_('Перетин часу цієї операції потребує іншої підтвердженої політики.'))
        try:
            shares = allocate_intervals(keys, relevant, production.company_id.currency_id.rounding)
        except ValueError as error:
            raise ValidationError(str(error)) from error
        return facts, relevant, shares

    @api.model
    def _freeze_incremental(self, production, keys, timer_members, selected, scope_key):
        self.check_access_rights('create')
        if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(keys) or not scope_key:
            raise ValidationError(_('Потрібні конкретні різні вироби й ключ розподілу.'))
        with self.env.cr.savepoint():
            self._lock_production(production)
            if production.state not in ('confirmed', 'progress') or production.product_qty != len(keys):
                raise ValidationError(_('Потрібен відкритий MO з повним незмінним поіменним складом.'))
            scopes = self._all_scopes(production)
            if scopes:
                self.env['quality.stock.posting']._reconcile(scopes[0])
            elif production.qty_produced:
                raise ValidationError(_('Непоіменний історичний випуск не можна прийняти за новий реєстр.'))
            data = self._fixed_data(production, keys, scopes)
            receipts = self.env['quality.stock.posting']._receipts(production)
            if set(selected) & set(receipts.mapped('unit_key')):
                raise ValidationError(_('Вибраний виріб уже випущено.'))
            observed, relevant, shares = self._incremental_facts(production, keys, timer_members, selected, scopes)
            data.update(timers=relevant, shares=shares, selected=sorted(selected),
                        observed_members={str(row['id']): row['members'] for row in observed})
            previous = scopes.filtered(lambda row: row.scope_key == scope_key)
            if previous:
                if previous.snapshot != data or previous.frozen_by_id != self.env.user:
                    raise ValidationError(_('Попередній запит розподілу змінився.'))
                return previous
            record = self.with_context(**{CORE_CONTEXT: CORE_TOKEN}).create(dict(
                production_id=production.id, policy=INCREMENTAL_POLICY, scope_key=scope_key, snapshot=data,
                frozen_by_id=self.env.uid, frozen_at=fields.Datetime.now(), **self._identity_values(production, keys)))
            return record.with_env(self.env)

    def _assert_current(self):
        if self.policy != INCREMENTAL_POLICY:
            return super()._assert_current()
        access(self)
        members = {int(key): value for key, value in self.snapshot['observed_members'].items()}
        _all, relevant, shares = self._incremental_facts(
            self.production_id, self.snapshot['keys'], members, self.snapshot['selected'], self._all_scopes(self.production_id))
        if relevant != self.snapshot['timers'] or shares != self.snapshot['shares']:
            raise ValidationError(_('Час вибраного виробу змінився після підготовки.'))


class StockIncrementalPosting(models.Model):
    _inherit = 'quality.stock.posting'

    @api.model
    def _check_closing_costs(self, scope):
        if scope.policy == INCREMENTAL_POLICY:
            scopes = self.env['quality.stock.cost.scope']._all_scopes(scope.production_id)
            expected = {(row['timer'], row['unit_key']): row['amount'] for item in scopes for row in item.snapshot['shares']}
            actual = self._receipts(scope.production_id).allocation_ids
            if expected != {(row.timer_id.id, row.unit_key): row.amount for row in actual}:
                raise ValidationError(_('Не всі частки часу узгоджено перед закриттям MO.'))

    @api.model
    def _finish_production(self, scope):
        self._check_closing_costs(scope)
        return super()._finish_production(scope)
