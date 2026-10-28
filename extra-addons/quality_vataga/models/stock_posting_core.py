"""Read-only historical stock schemas and guards; no posting engine."""

import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

from .quality_release_access import check_stock_release_roles
from .quality_archive import archive_disabled


# Historical scope policy: retained only for immutable timer guards.
POLICY = 'equal_closed_members_v1'


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
        archive_disabled()

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
    scope_key = fields.Char(readonly=True, copy=False)
    _sql_constraints = [('scope_request_unique', 'unique(production_id,scope_key)', 'Цей склад розподілу вже зафіксовано.')]

    def check_access_rule(self, operation):
        result = super().check_access_rule(operation)
        if self.ids:
            self.env.cr.execute('SELECT snapshot FROM quality_stock_cost_scope WHERE id IN %s', [tuple(self.ids)])
            timers = [row for (data,) in self.env.cr.fetchall() for row in data['timers']]
            for model, field in [('mrp.workcenter.productivity', 'id'), ('mrp.workorder', 'workorder'), ('mrp.workcenter', 'workcenter')]:
                access(self.env[model].browse(sorted({row[field] for row in timers})))
        return result


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
        ('timer_unit_unique', 'unique(timer_id,unit_key)', 'Час цього виробу вже нараховано іншим випуском.'),
        ('amount_nonnegative', 'check(amount >= 0)', 'Вартість частки не може бути від’ємною.'),
    ]

    @api.model
    def _protected_sources(self):
        return dict(super()._protected_sources(), posting_id='quality.stock.posting',
                    scope_id='quality.stock.cost.scope', timer_id='mrp.workcenter.productivity')


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
        if self.env.cr.fetchone():
            raise UserError(_('MO має архівні поіменні проведення. Нове проведення заблоковано до узгодженої міграції історії.'))
        # Native stock/company/ACL and 13.3 guards always run through super().
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
            self.env.cr.execute('SELECT 1 FROM quality_stock_cost_scope WHERE production_id=%s AND policy=%s LIMIT 1', [production.id, POLICY])
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
