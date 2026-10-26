"""Native MRP/Stock kernel, also tested without Enterprise business models.

Private APIs only. quality_stock_posting.py supplies the real QUNIT identities
and keeps the Enterprise bridge closed. No context boolean enables posting.
"""

import json

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools import float_compare

from .quality_release_access import check_stock_release_roles
from .stock_cost_allocator import POLICY, allocate_intervals
from .stock_release_plan_utils import exact_quantity, proportional_quantity


CORE_CONTEXT = 'quality_stock_kernel'
CORE_TOKEN = object()


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def access(records, operation='read'):
    records.check_access_rights(operation)
    records.check_access_rule(operation)


def production_sources(production, model, clause):
    records = production.env[model]
    records.flush_model()
    production.env.cr.execute('SELECT id FROM "%s" WHERE %s ORDER BY id' % (records._table, clause),
                              [production.id] * clause.count('%s'))
    records = records.browse([row[0] for row in production.env.cr.fetchall()])
    access(records)
    return records


def move_sources(moves, model, field):
    records = moves.env[model]
    records.flush_model()
    moves.env.cr.execute('SELECT id FROM "%s" WHERE "%s" IN %%s ORDER BY id' % (records._table, field), [tuple(moves.ids) or (0,)])
    records = records.browse([row[0] for row in moves.env.cr.fetchall()])
    access(records)
    return records


class StockPostingProtected(models.AbstractModel):
    _name = 'quality.stock.protected'
    _description = 'Незмінні підстави поштучного проведення'

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
        return super()._search(expression.AND([domain, [
            ('production_id', 'in', self.env['mrp.production']._search([])),
        ]]), offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid)

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            # Discover source identities without filtering away hidden rows;
            # every actual source read still requires its ordinary rules.
            for field, model in self._protected_sources().items():
                self.flush_recordset([field])
                self.env.cr.execute('SELECT DISTINCT "%s" FROM "%s" WHERE id IN %%s' % (field, self._table), [tuple(self.ids)])
                access(self.env[model].browse([row[0] for row in self.env.cr.fetchall() if row[0]]))
        return result

    @api.model
    def _protected_sources(self):
        return {'production_id': 'mrp.production'}

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(CORE_CONTEXT) is not CORE_TOKEN:
            raise UserError(_('Реєстр і розподіл створює тільки захищений складський адаптер.'))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(StockPostingProtected, self.with_context(context)).create(vals_list)

    def write(self, vals):
        raise UserError(_('Проведення та зафіксований розподіл незмінні.'))

    def unlink(self):
        raise UserError(_('Історія проведення та розподілу не видаляється.'))

    @api.model
    def _lock_production(self, production, operation='write'):
        access(production, operation)
        if production.company_id not in self.env.companies:
            raise AccessError(_('Недоступна компанія MO.'))
        # A real revision write detects stale REPEATABLE READ snapshots;
        # a SELECT FOR UPDATE alone would leave a waiter with old evidence.
        production.flush_recordset(['quality_stock_revision'])
        self.env.cr.execute('UPDATE mrp_production SET quality_stock_revision = COALESCE(quality_stock_revision, 0) + 1 WHERE id = %s', [production.id])
        self.env.flush_all()
        self.env.invalidate_all(flush=False)


class StockCostScope(models.Model):
    _name = 'quality.stock.cost.scope'
    _inherit = 'quality.stock.protected'
    _description = 'Зафіксована політика та склад виробничої вартості'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, compute_sudo=False)
    policy = fields.Char(required=True, readonly=True)
    snapshot = fields.Json(required=True, readonly=True)
    frozen_by_id = fields.Many2one('res.users', required=True, readonly=True, ondelete='restrict')
    frozen_at = fields.Datetime(required=True, readonly=True)
    _sql_constraints = [('production_unique', 'unique(production_id)', 'Розподіл вартості MO вже зафіксовано.')]

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT snapshot FROM quality_stock_cost_scope WHERE id IN %s', [tuple(self.ids)])
            timers = [row for (data,) in self.env.cr.fetchall() for row in data['timers']]
            for model, field in [('mrp.workcenter.productivity', 'id'), ('mrp.workorder', 'workorder'), ('mrp.workcenter', 'workcenter')]:
                access(self.env[model].browse(sorted({row[field] for row in timers})))
        return result

    @api.model
    def _identity_values(self, production, keys):
        return {}

    @api.model
    def _timers(self, production):
        self.env['mrp.workcenter.productivity'].flush_model()
        self.env.cr.execute('SELECT t.id FROM mrp_workcenter_productivity t JOIN mrp_workorder w ON w.id=t.workorder_id WHERE w.production_id=%s ORDER BY t.id FOR UPDATE OF t', [production.id])
        timers = self.env['mrp.workcenter.productivity'].browse([row[0] for row in self.env.cr.fetchall()])
        access(timers)
        access(timers.workorder_id)
        access(timers.workcenter_id)
        return timers

    @api.model
    def _timer_facts(self, timers, members):
        if set(members) != set(timers.ids):
            raise ValidationError(_('Кожен фактичний інтервал потребує явного поіменного складу; зайві або пропущені інтервали заборонено.'))
        return [dict(id=t.id, workorder=t.workorder_id.id, workcenter=t.workcenter_id.id,
                     start=fields.Datetime.to_string(t.date_start), end=fields.Datetime.to_string(t.date_end),
                     rate=t.workcenter_id.costs_hour, members=sorted(members[t.id])) for t in timers]

    @api.model
    def _freeze(self, production, unit_keys, timer_members):
        """Server-only explicit policy; missing or open time never means zero."""
        self.check_access_rights('create')
        with self.env.cr.savepoint():
            self._lock_production(production)
            if production.qty_produced or production.state not in ('confirmed', 'progress'):
                raise ValidationError(_('Розподіл фіксують до першого випуску відкритого MO.'))
            if production.product_qty != len(unit_keys):
                raise ValidationError(_('Потрібен повний поіменний склад MO: один ключ — одна одиниця.'))
            if not production.bom_id or production.bom_id.type != 'normal':
                raise ValidationError(_('Потрібна однозначна звичайна виробнича BOM.'))
            timers = self._timers(production)
            if any(timer.company_id != production.company_id or timer.workorder_id.company_id != production.company_id for timer in timers):
                raise ValidationError(_('Робочі інтервали іншої компанії не можуть входити до розподілу.'))
            workorders = production_sources(production, 'mrp.workorder', 'production_id = %s')
            if not workorders or any(not timers.filtered(lambda timer: timer.workorder_id == wo) for wo in workorders):
                raise ValidationError(_('Відсутність робочого інтервалу операції не є доказом нульової вартості.'))
            facts = self._timer_facts(timers, timer_members)
            try:
                shares = allocate_intervals(unit_keys, facts, production.company_id.currency_id.rounding)
            except ValueError as error:
                raise ValidationError(str(error)) from error
            raw = production_sources(production, 'stock.move', 'raw_material_production_id = %s').filtered(lambda move: move.state != 'cancel')
            access(raw)
            components = []
            for move in raw:
                per_unit = proportional_quantity(move.product_uom_qty, 1, len(unit_keys), move.product_uom.rounding)
                components.append(dict(bom_line=move.bom_line_id.id, product=move.product_id.id,
                                       uom=move.product_uom.id, total=move.product_uom_qty, per_unit=per_unit))
            if not components or len({row['bom_line'] for row in components}) != len(components) or any(not row['bom_line'] for row in components):
                raise ValidationError(_('Потрібні однозначні компоненти BOM без додаткових або об’єднаних рухів.'))
            scope = self.with_context(**{CORE_CONTEXT: CORE_TOKEN}).create(dict(
                production_id=production.id, policy=POLICY, frozen_by_id=self.env.uid, frozen_at=fields.Datetime.now(),
                snapshot=dict(keys=sorted(unit_keys), timers=facts, shares=shares, components=components,
                              quantity=production.product_qty, product=production.product_id.id, bom=production.bom_id.id,
                              uom=production.product_uom_id.id, company=production.company_id.id,
                              currency=production.company_id.currency_id.id, rounding=production.company_id.currency_id.rounding),
                **self._identity_values(production, unit_keys),
            ))
            return scope.with_env(self.env)

    def _assert_current(self):
        self.ensure_one()
        access(self)
        timers = self._timers(self.production_id)
        members = {row['id']: row['members'] for row in self.snapshot['timers']}
        if self.policy != POLICY or self._timer_facts(timers, members) != self.snapshot['timers']:
            raise ValidationError(_('Інтервали або ставки змінилися після фіксації. Коригування вже проведеної вартості не підтримується.'))


class StockPosting(models.Model):
    _name = 'quality.stock.posting'
    _inherit = 'quality.stock.protected'
    _description = 'Незмінний фактичний випуск одного виробу'

    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, compute_sudo=False)
    scope_id = fields.Many2one('quality.stock.cost.scope', required=True, readonly=True, ondelete='restrict')
    unit_key = fields.Char(required=True, readonly=True, index=True)
    permission_key = fields.Char(required=True, readonly=True, index=True)
    request_key = fields.Char(required=True, readonly=True)
    finished_move_id = fields.Many2one('stock.move', required=True, readonly=True, ondelete='restrict')
    finished_line_id = fields.Many2one('stock.move.line', required=True, readonly=True, ondelete='restrict')
    line_ids = fields.One2many('quality.stock.posting.line', 'posting_id', readonly=True)
    allocation_ids = fields.One2many('quality.stock.cost.allocation', 'posting_id', readonly=True)
    component_cost = fields.Float(required=True, readonly=True)
    labour_cost = fields.Float(required=True, readonly=True)
    posted_by_id = fields.Many2one('res.users', required=True, readonly=True, ondelete='restrict')
    posted_at = fields.Datetime(required=True, readonly=True)
    _sql_constraints = [
        ('unit_unique', 'unique(unit_key)', 'Цей виріб вже випущено.'),
        ('permission_unique', 'unique(permission_key)', 'Цей дозвіл вже використано.'),
        ('request_unique', 'unique(production_id,request_key)', 'Цей запит вже проведено.'),
        ('finished_unique', 'unique(finished_line_id)', 'Цю готову одиницю вже зареєстровано.'),
    ]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), scope_id='quality.stock.cost.scope',
                    finished_move_id='stock.move', finished_line_id='stock.move.line')

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT move_line_id, valuation_layer_id FROM quality_stock_posting_line WHERE posting_id IN %s', [tuple(self.ids)])
            rows = self.env.cr.fetchall()
            access(self.env['stock.move.line'].browse([row[0] for row in rows]))
            access(self.env['stock.valuation.layer'].browse([row[1] for row in rows]))
        return result

    @api.constrains('finished_line_id', 'finished_move_id', 'production_id', 'scope_id')
    def _check_done(self):
        for row in self:
            move, line = row.finished_move_id, row.finished_line_id
            access(move); access(line)
            if (move.state != 'done' or line.state != 'done' or line.move_id != move
                    or move.production_id != row.production_id or row.scope_id.production_id != row.production_id
                    or move.company_id != row.company_id or move.product_id != row.production_id.product_id
                    or line.product_id != move.product_id or line.product_uom_id != move.product_uom
                    or line.quantity != 1 or len(move.move_line_ids) != 1):
                raise ValidationError(_('Реєстр приймає тільки фактичну проведену одиницю відповідного MO.'))

    @api.constrains('line_ids', 'allocation_ids', 'component_cost', 'labour_cost')
    def _check_cost_conservation(self):
        for receipt in self:
            component_layers = receipt.line_ids.filtered(lambda row: row.kind == 'component').valuation_layer_id
            finished = receipt.line_ids.filtered(lambda row: row.kind == 'finished')
            currency = receipt.company_id.currency_id
            if (len(finished) != 1 or finished.move_line_id != receipt.finished_line_id
                    or not component_layers or receipt.component_cost < 0
                    or not currency.is_zero(receipt.component_cost + sum(component_layers.mapped('value')))
                    or not currency.is_zero(receipt.labour_cost - sum(receipt.allocation_ids.mapped('amount')))
                    or not currency.is_zero(finished.valuation_layer_id.value - receipt.component_cost - receipt.labour_cost)):
                raise ValidationError(_('Кількості та фактична вартість реєстру не узгоджені.'))

    @api.model
    def _check_integration_contract(self, scope):
        # The actual Enterprise inheritance overrides this with an absolute
        # gate. The standalone native kernel has no QUNIT authorization API.
        if 'quality.production.unit' in self.env:
            raise UserError(_('Потрібен перевірений контракт Enterprise для QUNIT.'))

    @api.model
    def _posting_identity_values(self, scope, unit_key, permission_key, plan):
        if plan is not None:
            raise ValidationError(_('План QUNIT потребує Enterprise-моста.'))
        return {}

    @api.model
    def _receipts(self, production):
        self.flush_model()
        self.env.cr.execute('SELECT id FROM quality_stock_posting WHERE production_id=%s ORDER BY id', [production.id])
        records = self.browse([row[0] for row in self.env.cr.fetchall()])
        access(records)
        return records

    @api.model
    def _reconcile(self, scope):
        production = scope.production_id
        receipts = self._receipts(production)
        all_moves = production_sources(production, 'stock.move', 'production_id = %s OR raw_material_production_id = %s')
        done = all_moves.filtered(lambda move: move.production_id == production and move.state == 'done')
        raw_done = all_moves.filtered(lambda move: move.raw_material_production_id == production and move.state == 'done')
        move_sources(raw_done | done, 'stock.move.line', 'move_id')
        move_sources(raw_done | done, 'stock.valuation.layer', 'stock_move_id')
        if (set(done.ids) != set(receipts.finished_move_id.ids) or production.qty_produced != len(receipts)
                or set(raw_done.move_line_ids.ids) != set(receipts.line_ids.filtered(lambda row: row.kind == 'component').move_line_id.ids)):
            raise ValidationError(_('Складська історія MO не відповідає поіменному реєстру.'))
        for receipt in receipts:
            components = receipt.line_ids.filtered(lambda row: row.kind == 'component').move_line_id
            if receipt.finished_line_id.consume_line_ids != components or components.produce_line_ids != receipt.finished_line_id:
                raise ValidationError(_('Історичні складські зв’язки виробу змінено.'))
            for move in receipt.line_ids.move_id:
                if set(move.stock_valuation_layer_ids.ids) != set(receipt.line_ids.filtered(lambda row: row.move_id == move).valuation_layer_id.ids):
                    raise ValidationError(_('Додаткові коригування оцінки потребують окремого механізму звірки.'))
        return receipts

    @api.model
    def _post_one(self, scope, unit_key, permission_key, request_key, plan=None):
        """One atomic native posting; private and NOT enabled for Enterprise."""
        self._check_integration_contract(scope)
        self.check_access_rights('create')
        production = scope.production_id
        if not all(isinstance(key, str) and 0 < len(key) <= 128 for key in (unit_key, permission_key, request_key)):
            raise ValidationError(_('Потрібні однозначні ключі виробу, дозволу та запиту.'))
        with self.env.cr.savepoint():
            self._lock_production(production)
            receipts = self._receipts(production)
            previous = receipts.filtered(lambda row: row.request_key == request_key)
            if previous:
                if (previous.scope_id != scope or previous.unit_key != unit_key
                        or previous.permission_key != permission_key or previous.posted_by_id != self.env.user):
                    raise ValidationError(_('Повторний запит має інший склад або автора.'))
                self._reconcile(scope)
                return previous
            if receipts.filtered(lambda row: row.unit_key == unit_key or row.permission_key == permission_key):
                raise ValidationError(_('Виріб або дозвіл вже використано.'))
            self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE unit_key=%s OR permission_key=%s LIMIT 1', [unit_key, permission_key])
            if self.env.cr.fetchone():
                raise ValidationError(_('Виріб або дозвіл вже використано в іншому проведенні.'))
            scope._assert_current()
            self._reconcile(scope)
            identities = self._posting_identity_values(scope, unit_key, permission_key, plan)
            data = scope.snapshot
            if (unit_key not in data['keys'] or production.product_qty != data['quantity']
                    or production.product_id.id != data['product'] or production.bom_id.id != data['bom']
                    or production.product_uom_id.id != data['uom'] or production.state not in ('confirmed', 'progress')
                    or production.company_id.id != data['company'] or production.company_id.currency_id.id != data['currency']
                    or production.company_id.currency_id.rounding != data['rounding']
                    or production.extra_cost or production.product_id.tracking != 'none'
                    or production.product_id.cost_method not in ('fifo', 'average')
                    or production.product_id.valuation != 'manual_periodic'):
                raise ValidationError(_('Поточний MO, облік або ідентифікація не відповідають перевіреному обмеженому контракту.'))
            unit_uom = self.env.ref('uom.product_uom_unit')
            if production.product_uom_id != unit_uom:
                raise ValidationError(_('Адаптер підтримує рівно одну складську одиницю на виріб.'))
            moves = production_sources(production, 'stock.move', 'production_id = %s OR raw_material_production_id = %s')
            raw = moves.filtered(lambda move: move.raw_material_production_id == production and move.state not in ('done', 'cancel'))
            finished = moves.filtered(lambda move: move.production_id == production and move.state not in ('done', 'cancel'))
            access(raw, 'write'); access(finished, 'write')
            access(move_sources(raw | finished, 'stock.move.line', 'move_id'), 'write')
            if len(raw) != len(data['components']) or len(finished) != 1 or finished.product_id != production.product_id:
                raise ValidationError(_('Неоднозначні рухи, побічна продукція або невідомий залишок MO.'))
            if (finished.product_uom != unit_uom or finished.product_uom_qty != len(data['keys']) - len(receipts)
                    or finished.location_id != production.production_location_id or finished.location_dest_id != production.location_dest_id
                    or finished.company_id != production.company_id):
                raise ValidationError(_('Готовий рух не відповідає кількості, складу або компанії MO.'))
            expected = production._get_moves_raw_values()
            if len(expected) != len(data['components']) or any(not any(
                    row['bom_line_id'] == fact['bom_line'] and row['product_id'] == fact['product']
                    and row['product_uom'] == fact['uom'] and row['product_uom_qty'] == fact['total']
                    for row in expected) for fact in data['components']):
                raise ValidationError(_('BOM змінилася після фіксації розподілу.'))
            for move in raw:
                facts = [row for row in data['components'] if row['bom_line'] == move.bom_line_id.id]
                if len(facts) != 1:
                    raise ValidationError(_('Компонент не відповідає зафіксованій BOM.'))
                fact = facts[0]
                access(move.move_line_ids, 'write')
                if (move.product_id.id != fact['product'] or move.product_uom.id != fact['uom']
                        or move.product_uom_qty != exact_quantity(fact['per_unit'] * (len(data['keys']) - len(receipts)), move.product_uom.rounding)
                        or move.state != 'assigned' or move.manual_consumption or move.product_id.tracking != 'none'
                        or move.product_id.type != 'product' or move.company_id != production.company_id
                        or move.product_id.valuation != 'manual_periodic'
                        or move.location_id != production.location_src_id or move.location_dest_id != production.production_location_id
                        or any(line.lot_id or line.owner_id or line.package_id or line.result_package_id
                               or line.location_id != move.location_id or line.location_dest_id != move.location_dest_id
                               for line in move.move_line_ids)
                        or float_compare(move.quantity, move.product_uom_qty, precision_rounding=move.product_uom.rounding)):
                    raise ValidationError(_('Потрібен повний справжній резерв, незмінна BOM та автоматичний розхід без lot/serial, власника або пакування.'))
            # Select quantities on real reserved moves; native stock splits
            # remaining demand and creates real valuation layers normally.
            for move in raw:
                fact = next(row for row in data['components'] if row['bom_line'] == move.bom_line_id.id)
                move.write({'quantity': fact['per_unit'], 'picked': True})
            consumed = raw.with_context(**{CORE_CONTEXT: CORE_TOKEN})._action_done(cancel_backorder=False)
            component_layers = move_sources(consumed, 'stock.valuation.layer', 'stock_move_id')
            component_cost = -sum(component_layers.mapped('value'))
            shares = [row for row in data['shares'] if row['unit_key'] == unit_key]
            labour = sum(row['amount'] for row in shares)
            finished.write({'quantity': 1, 'picked': True, 'price_unit': component_cost + labour})
            produced = finished.with_context(**{CORE_CONTEXT: CORE_TOKEN})._action_done(cancel_backorder=False)
            if len(produced) != 1 or len(produced.move_line_ids) != 1:
                raise ValidationError(_('Очікується одна проведена готова одиниця.'))
            # Set BOTH native traceability directions through the native M2M
            # only for this new finished line, never all MO finished lines.
            produced.move_line_ids.consume_line_ids = [Command.set(consumed.move_line_ids.ids)]
            move_sources(produced, 'stock.valuation.layer', 'stock_move_id')
            if (len(produced.stock_valuation_layer_ids) != 1
                    or not production.company_id.currency_id.is_zero(
                        sum(produced.stock_valuation_layer_ids.mapped('value')) - component_cost - labour)):
                raise ValidationError(_('Фактична оцінка готового виробу не відповідає витратам.'))
            lines = []
            for kind, moves in [('component', consumed), ('finished', produced)]:
                for move in moves:
                    if len(move.stock_valuation_layer_ids) != 1:
                        raise ValidationError(_('Коригування або неоднозначні valuation layers поки не підтримуються.'))
                    layer = move.stock_valuation_layer_ids
                    for line in move.move_line_ids:
                        lines.append(Command.create(dict(kind=kind, move_line_id=line.id, valuation_layer_id=layer.id,
                                                         quantity=line.quantity, uom_id=line.product_uom_id.id)))
            receipt = self.with_context(**{CORE_CONTEXT: CORE_TOKEN}).create(dict(
                production_id=production.id, scope_id=scope.id, unit_key=unit_key, permission_key=permission_key,
                request_key=request_key, finished_move_id=produced.id, finished_line_id=produced.move_line_ids.id,
                component_cost=component_cost, labour_cost=labour, posted_by_id=self.env.uid, posted_at=fields.Datetime.now(),
                line_ids=lines, allocation_ids=[Command.create(dict(scope_id=scope.id, timer_id=row['timer'],
                                                                  unit_key=unit_key, amount=row['amount'])) for row in shares],
                **identities,
            ))
            self._reconcile(scope)
            if len(receipts) + 1 == len(data['keys']):
                # All actual stock is reconciled first; never call native
                # button_mark_done/_post_inventory again to close this MO.
                production.workorder_ids.button_finish()
                production.write({'date_finished': fields.Datetime.now(), 'is_locked': True})
            production.message_post(body=_('Проведено виріб %(unit)s; реєстр %(record)s.', unit=unit_key, record=receipt.id),
                                    subtype_xmlid='mail.mt_note')
            return receipt.with_env(self.env)


class StockPostingLine(models.Model):
    _name = 'quality.stock.posting.line'
    _inherit = 'quality.stock.protected'
    _description = 'Фактична готова продукція та споживання'

    posting_id = fields.Many2one('quality.stock.posting', required=True, readonly=True, ondelete='restrict', index=True)
    production_id = fields.Many2one(related='posting_id.production_id', store=True, compute_sudo=False)
    company_id = fields.Many2one(related='posting_id.company_id', store=True, compute_sudo=False)
    kind = fields.Selection([('component', 'Компонент'), ('finished', 'Готовий виріб')], required=True, readonly=True)
    move_line_id = fields.Many2one('stock.move.line', required=True, readonly=True, ondelete='restrict')
    move_id = fields.Many2one(related='move_line_id.move_id', store=True, compute_sudo=False)
    valuation_layer_id = fields.Many2one('stock.valuation.layer', required=True, readonly=True, ondelete='restrict')
    quantity = fields.Float(required=True, readonly=True)
    uom_id = fields.Many2one('uom.uom', required=True, readonly=True, ondelete='restrict')
    _sql_constraints = [('line_unique', 'unique(move_line_id)', 'Фактичну складську строку вже використано.')]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), posting_id='quality.stock.posting',
                    move_line_id='stock.move.line', valuation_layer_id='stock.valuation.layer')

    @api.constrains('move_line_id', 'valuation_layer_id', 'quantity', 'kind', 'posting_id')
    def _check_fact(self):
        for row in self:
            line, move, layer = row.move_line_id, row.move_id, row.valuation_layer_id
            access(line); access(layer)
            mo = move.raw_material_production_id if row.kind == 'component' else move.production_id
            if (line.state != 'done' or mo != row.production_id or layer.stock_move_id != move
                    or layer.company_id != row.company_id or line.quantity != row.quantity or row.quantity <= 0
                    or line.product_uom_id != row.uom_id):
                raise ValidationError(_('Рядок реєстру повинен точно відповідати проведеному складському факту.'))


class StockCostAllocation(models.Model):
    _name = 'quality.stock.cost.allocation'
    _inherit = 'quality.stock.protected'
    _description = 'Одноразово поглинута частка робочого інтервалу'

    posting_id = fields.Many2one('quality.stock.posting', required=True, readonly=True, ondelete='restrict')
    production_id = fields.Many2one(related='posting_id.production_id', store=True, compute_sudo=False)
    company_id = fields.Many2one(related='posting_id.company_id', store=True, compute_sudo=False)
    scope_id = fields.Many2one('quality.stock.cost.scope', required=True, readonly=True, ondelete='restrict')
    timer_id = fields.Many2one('mrp.workcenter.productivity', required=True, readonly=True, ondelete='restrict')
    unit_key = fields.Char(required=True, readonly=True)
    amount = fields.Float(required=True, readonly=True)
    _sql_constraints = [
        ('share_unique', 'unique(scope_id,timer_id,unit_key)', 'Частку часу вже нараховано.'),
        ('amount_nonnegative', 'check(amount >= 0)', 'Вартість частки не може бути від’ємною.'),
    ]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), posting_id='quality.stock.posting',
                    scope_id='quality.stock.cost.scope', timer_id='mrp.workcenter.productivity')

    @api.constrains('scope_id', 'timer_id', 'unit_key', 'amount', 'posting_id')
    def _check_share(self):
        for row in self:
            expected = [item for item in row.scope_id.snapshot['shares']
                        if item['timer'] == row.timer_id.id and item['unit_key'] == row.unit_key]
            if (row.scope_id != row.posting_id.scope_id or row.unit_key != row.posting_id.unit_key
                    or len(expected) != 1 or row.amount != expected[0]['amount']):
                raise ValidationError(_('Частка витрат не відповідає зафіксованій політиці та виробу.'))


class MrpStockKernelBoundary(models.Model):
    _inherit = 'mrp.production'

    quality_stock_revision = fields.Integer(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if any('quality_stock_revision' in vals for vals in vals_list):
            raise UserError(_('Службову ревізію складського реєстру встановлює система.'))
        context = dict(self.env.context)
        context.pop('default_quality_stock_revision', None)
        return super(MrpStockKernelBoundary, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if 'quality_stock_revision' in vals:
            raise UserError(_('Службова ревізія складського реєстру захищена.'))
        return super().write(vals)

    def _post_inventory(self, cancel_backorder=False):
        for production in self.sorted('id'):
            self.env['quality.stock.posting']._lock_production(production, operation='read')
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE production_id IN %s LIMIT 1', [tuple(self.ids) or (0,)])
        if self.env.cr.fetchone():
            raise UserError(_('MO має поіменні проведення. Повторний стандартний _post_inventory заборонено.'))
        return super()._post_inventory(cancel_backorder=cancel_backorder)


def journal_has(env, field, ids):
    if not ids:
        return False
    env['quality.stock.posting.line'].flush_model()
    # field is a private literal from the model overrides below.
    env.cr.execute('SELECT 1 FROM quality_stock_posting_line WHERE "%s" IN %%s LIMIT 1' % field, [tuple(ids)])
    return bool(env.cr.fetchone())


class StockJournalMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        productions = self.production_id | self.raw_material_production_id
        for production in productions.sorted('id'):
            self.env['quality.stock.posting']._lock_production(production, operation='read')
        self.env['quality.stock.posting'].flush_model()
        self.env.cr.execute('SELECT 1 FROM quality_stock_posting WHERE production_id IN %s LIMIT 1', [tuple(productions.ids) or (0,)])
        if self.env.cr.fetchone() and self.env.context.get(CORE_CONTEXT) is not CORE_TOKEN:
            raise UserError(_('Наступне проведення цього MO виконує тільки захищений поштучний адаптер.'))
        # Only our additional boundary is conditional. Native stock, company,
        # ACL and quality_vataga 13.3 guards always run through super().
        return super()._action_done(cancel_backorder=cancel_backorder)

    def write(self, vals):
        if {'product_id', 'company_id', 'product_uom', 'product_uom_qty', 'quantity', 'picked', 'price_unit',
            'location_id', 'location_dest_id', 'production_id', 'raw_material_production_id',
            'move_line_ids', 'state'}.intersection(vals) and journal_has(self.env, 'move_id', self.ids):
            raise UserError(_('Проведений рух поіменного реєстру незмінний.'))
        return super().write(vals)

    def unlink(self):
        if journal_has(self.env, 'move_id', self.ids):
            raise UserError(_('Рух поіменного реєстру не видаляється.'))
        return super().unlink()


class StockJournalMoveLine(models.Model):
    _inherit = 'stock.move.line'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if journal_has(self.env, 'move_id', [vals.get('move_id', self.env.context.get('default_move_id')) or 0]):
                raise UserError(_('Нові строки не додають до руху незмінного реєстру.'))
            for name in ('consume_line_ids', 'produce_line_ids'):
                targets = []
                for command in vals.get(name, self.env.context.get('default_' + name, [])):
                    if command[0] in (1, 2, 3, 4):
                        targets.append(command[1])
                    elif command[0] == 6:
                        targets.extend(command[2])
                if journal_has(self.env, 'move_line_id', targets):
                    raise UserError(_('Новий складський рядок не може переприв’язати історичне споживання.'))
        return super().create(vals_list)

    def write(self, vals):
        targets = set(self.ids)
        for name in ('consume_line_ids', 'produce_line_ids'):
            if name in vals:
                targets.update(self[name].ids)
                for command in vals[name]:
                    if command[0] in (1, 2, 3, 4):
                        targets.add(command[1])
                    elif command[0] == 6:
                        targets.update(command[2])
        if journal_has(self.env, 'move_line_id', targets):
            raise UserError(_('Фактична строка та історичні consume/produce зв’язки поіменного реєстру незмінні.'))
        return super().write(vals)

    def unlink(self):
        if journal_has(self.env, 'move_line_id', self.ids):
            raise UserError(_('Строка поіменного реєстру не видаляється.'))
        return super().unlink()


class StockJournalValuation(models.Model):
    _inherit = 'stock.valuation.layer'

    def write(self, vals):
        # Native FIFO remaining_qty/value still evolve on later deliveries;
        # original quantity/value/unit_cost and source identity never do.
        if {'quantity', 'value', 'unit_cost', 'stock_move_id', 'product_id', 'company_id',
            'account_move_id', 'account_move_line_id', 'stock_valuation_layer_id'}.intersection(vals) and journal_has(self.env, 'valuation_layer_id', self.ids):
            raise UserError(_('Проведену оцінку поіменного реєстру не переписують.'))
        return super().write(vals)

    def unlink(self):
        if journal_has(self.env, 'valuation_layer_id', self.ids):
            raise UserError(_('Оцінка поіменного реєстру не видаляється.'))
        return super().unlink()


class StockCostTimerBoundary(models.Model):
    _inherit = 'mrp.workcenter.productivity'

    @api.model_create_multi
    def create(self, vals_list):
        workorders = self.env['mrp.workorder'].browse(sorted({
            vals.get('workorder_id', self.env.context.get('default_workorder_id')) for vals in vals_list
            if vals.get('workorder_id', self.env.context.get('default_workorder_id'))
        }))
        for production in workorders.production_id.sorted('id'):
            self.env['quality.stock.posting']._lock_production(production, operation='read')
            self.env.cr.execute('SELECT 1 FROM quality_stock_cost_scope WHERE production_id=%s LIMIT 1', [production.id])
            if self.env.cr.fetchone():
                raise UserError(_('Вартість MO вже зафіксовано. Нові інтервали потребують окремої процедури коригування; автоматичне донарахування заблоковано.'))
        return super().create(vals_list)

    def _assert_not_frozen(self):
        self.env['quality.stock.cost.scope'].flush_model()
        for timer_id in self.ids:
            self.env.cr.execute("SELECT 1 FROM quality_stock_cost_scope WHERE snapshot->'timers' @> %s::jsonb LIMIT 1", [packed([dict(id=timer_id)])])
            if self.env.cr.fetchone():
                raise UserError(_('Інтервал включено до незмінного розподілу вартості. Зміна потребує окремого сумісного механізму коригування.'))

    def write(self, vals):
        if {'date_start', 'date_end', 'workorder_id', 'workcenter_id', 'company_id', 'loss_id', 'duration'}.intersection(vals):
            self._assert_not_frozen()
        return super().write(vals)

    def unlink(self):
        self._assert_not_frozen()
        return super().unlink()
