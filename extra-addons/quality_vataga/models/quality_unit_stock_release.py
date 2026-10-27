import json

from markupsafe import escape

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .quality_release_access import check_stock_release_roles, has_stock_release_roles
from .stock_release_plan_utils import (
    StockReleasePlanError, allocate_reserved_quantity, exact_quantity, proportional_quantity,
)
from .stock_release_diagnostics import posting_blocker_codes


PLAN_CONTEXT = 'quality_vataga_stock_release_plan'
PLAN_TOKEN = object()
POSTING_UNAVAILABLE = (
    'Складське проведення QUNIT заблоковано до перевірки Enterprise-моста. '
    'Реєстр, розподільник закритих інтервалів та складське ядро реалізовано; '
    'інкрементальна вартість, повторна перевірка допусків і продовження 13.3/13.4А реалізовані, '
    'але ввімкнення очікує ізольованих Enterprise-тестів. '
    'Підготовлений план не є складським випуском та не змінює залишки.'
)


def _snapshot(values):
    return json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


class QualityUnitStockReleasePlan(models.Model):
    _name = 'quality.unit.stock.release.plan'
    _inherit = 'quality.release.access'
    _description = 'Незмінний план складського випуску QUNIT, без проведення'
    _order = 'id desc'

    name = fields.Char(string='План випуску', required=True, readonly=True, copy=False)
    production_id = fields.Many2one('mrp.production', string='MO', required=True, readonly=True, ondelete='restrict', index=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True, readonly=True, compute_sudo=False)
    permission_ids = fields.Many2many(
        'quality.unit.release.line', 'quality_stock_plan_permission_rel', 'plan_id', 'permission_id',
        string='Дозволи 13.4А', readonly=True,
    )
    quantity = fields.Integer(string='Запропоновано виробів', required=True, readonly=True)
    prepared_by_id = fields.Many2one('res.users', string='Підготував', required=True, readonly=True, ondelete='restrict')
    prepared_at = fields.Datetime(string='Дата підготовки', required=True, readonly=True)
    request_key = fields.Char(required=True, readonly=True, copy=False)
    evidence_snapshot = fields.Text(string='Підстави та резерви на момент підготовки', required=True, readonly=True)
    component_summary = fields.Text(string='Пропозиція часткового споживання', required=True, readonly=True)
    posting_note = fields.Text(string='Стан проведення', compute='_compute_posting_note')
    posting_diagnostic = fields.Text(string='Перешкоди складського проведення', compute='_compute_posting_diagnostic')

    _sql_constraints = [
        ('request_unique', 'unique(production_id, request_key)', 'Цей запит підготовки вже оброблено.'),
        ('quantity_positive', 'check(quantity > 0)', 'План потребує конкретних виробів.'),
    ]

    def _compute_posting_note(self):
        for plan in self:
            plan.posting_note = _(POSTING_UNAVAILABLE)

    @api.depends('evidence_snapshot')
    def _compute_posting_diagnostic(self):
        for plan in self:
            # Describe the immutable preparation facts, not a current cost
            # estimate or certification. Older snapshots remain untouched.
            try:
                costing = json.loads(plan.evidence_snapshot or '{}').get('costing', {})
                timers = costing.get('timers', [])
                if not isinstance(timers, list) or any(not isinstance(timer, dict) for timer in timers):
                    raise ValueError('Invalid timer snapshot')
                codes = posting_blocker_codes(timers)
                contract = costing.get('native_contract', {})
                if contract:
                    codes = [code for code in codes if code != 'unmapped_intervals']
                    if any(timer['missing_scope'] for timer in contract['timers']):
                        codes.append('unmapped_intervals')
                    if any(timer['employee_costing'] for timer in contract['timers']):
                        codes.append('employee_contract_pending')
                    if contract['profile'] == 'standard_real_time_v1':
                        codes.append('standard_real_time')
            except (ValueError, TypeError, AttributeError, KeyError):
                codes = posting_blocker_codes([]) + ['legacy_snapshot']
            labels = {
                'enterprise_bridge_pending': _('Реєстр quality.stock.posting реалізовано. Enterprise-міст QUNIT → дозвіл 13.4А → план залишається серверно заблокованим до інтеграційної перевірки.'),
                'interval_scope_required': _('Інкрементальний розподільник equal_closed_members_v2 фіксує лише закритий час вибраного QUNIT. Інші вироби продовжують роботу; спільні відкриті або неоднозначні інтервали блокують пов’язані вироби.'),
                'native_post_inventory_forbidden': _('Складське ядро встановлює consume_line_ids тільки для нової одиниці й захищає обидва напрямки зв’язку. Стандартний _post_inventory після реєстрації випуску заборонено.'),
                'enterprise_validation_pending': _('Потрібна інтеграційна перевірка на окремій Odoo Enterprise, включно з оцінкою запасів та бухгалтерськими проведеннями.'),
                'open_intervals': _('У знімку є відкриті робочі інтервали. Вартість окремого завершеного виробу ще не зафіксована.'),
                'unmapped_intervals': _('У знімку є інтервали без поіменного робочого запуску QUNIT або старий знімок без такої прив’язки. Належність часу виробам не визначена.'),
                'legacy_snapshot': _('Знімок не містить придатних даних діагностики. Його значення не змінено.'),
                'employee_contract_pending': _('Інтервали містять працівників. Формулу штатного Enterprise-обліку праці та її поштучний розподіл ще не підтверджено; підміна тарифом робочого центру заборонена.'),
                'standard_real_time': _('Профіль Standard + автоматична оцінка: готовий SVL за нормативом, проводки лише штатні. Різниця фактичних витрат і нормативу є окремим показником, а не автоматичною додатковою проводкою.'),
            }
            plan.posting_diagnostic = '\n\n'.join(labels[code] for code in codes)

    @api.model
    def check_access_rights(self, operation, raise_exception=True):
        if not check_stock_release_roles(self.env.user, raise_exception=raise_exception):
            return False
        return super().check_access_rights(operation, raise_exception=raise_exception)

    def check_access_rule(self, operation):
        check_stock_release_roles(self.env.user)
        result = super().check_access_rule(operation)
        if self.ids:
            # Read protective relation IDs directly: normal M2M reads could
            # omit hidden children. This confers no access to those records.
            self.env.cr.execute('SELECT DISTINCT permission_id FROM quality_stock_plan_permission_rel WHERE plan_id IN %s', [tuple(self.ids)])
            permissions = self.env['quality.unit.release.line'].browse([row[0] for row in self.env.cr.fetchall()])
            permissions.check_access_rights('read')
            permissions.check_access_rule('read')
            # Extract only protective identities from the immutable snapshot.
            # The snapshot itself is never returned before the normal rules
            # on all source documents have allowed access.
            sources = {name: set() for name in (
                'stock.move', 'stock.move.line', 'mrp.workorder', 'mrp.workcenter.productivity',
            )}
            self.env.cr.execute('SELECT evidence_snapshot FROM quality_unit_stock_release_plan WHERE id IN %s', [tuple(self.ids)])
            for (snapshot,) in self.env.cr.fetchall():
                try:
                    data = json.loads(snapshot)
                    sources['stock.move'].update(row['move_id'] for row in data['components'])
                    sources['stock.move'].add(data['finished']['move'])
                    sources['stock.move.line'].update(line['id'] for row in data['components'] for line in row['source_lines'])
                    sources['stock.move.line'].update(line['id'] for line in data['finished']['source_lines'])
                    sources['mrp.workorder'].update(row['id'] for row in data['costing']['workorders'])
                    sources['mrp.workcenter.productivity'].update(row['id'] for row in data['costing']['timers'])
                except (KeyError, ValueError, TypeError):
                    raise AccessError(_('Неможливо перевірити доступ до джерел збереженого плану.')) from None
            for model, record_ids in sources.items():
                records = self.env[model].browse(sorted(record_ids))
                records.check_access_rights('read')
                records.check_access_rule('read')
        return result

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get(PLAN_CONTEXT) is not PLAN_TOKEN:
            raise UserError(_('План створюється лише захищеною підготовкою конкретних QUNIT.'))
        # Even an internal caller supplies only identities. Never trust a
        # client snapshot, quantity, stock state, inspector, or author.
        prepared = []
        for vals in vals_list:
            if set(vals) != {'production_id', 'permission_ids', 'request_key'}:
                raise UserError(_('Реквізити та кількості плану встановлює система.'))
            production = self.env['mrp.production'].browse(vals['production_id'])
            permissions = self.env['quality.unit.release.line'].browse(vals['permission_ids'])
            data = production._get_unit_stock_release_proposal(permissions)
            prepared.append(dict(
                production_id=production.id, permission_ids=[Command.set(permissions.ids)],
                request_key=vals['request_key'], quantity=len(permissions),
                name=self.env.ref('quality_vataga.sequence_quality_unit_stock_release_plan').next_by_id(),
                prepared_by_id=self.env.uid, prepared_at=fields.Datetime.now(),
                evidence_snapshot=_snapshot(data), component_summary='\n'.join(
                    _('%(product)s: %(quantity)s %(uom)s; залишиться %(remaining)s; рух %(move)s; резерви %(lines)s',
                      product=row['product_name'], quantity=row['quantity'], uom=row['uom_name'],
                      remaining=row['remaining_quantity'], move=row['move_id'],
                      lines=', '.join(str(item['line_id']) for item in row['reservations']))
                    for row in data['components']
                ),
            ))
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        return super(QualityUnitStockReleasePlan, self.with_context(context)).create(prepared)

    def write(self, vals):
        raise UserError(_('План є незмінним знімком, а не складським документом. Підготуйте новий план.'))

    def unlink(self):
        raise UserError(_('Історія підготовки випуску не видаляється.'))

    @api.model
    def _prepare(self, production, permissions, request_key):
        production._check_unit_stock_release_access()
        self.check_access_rights('create')
        if not request_key or not isinstance(request_key, str) or len(request_key) > 128:
            raise ValidationError(_('Потрібен коректний ключ запиту підготовки.'))
        # Reject foreign identities before taking locks, keeping MO -> QRUN
        # lock order consistent with admissions and execution.
        production._check_stock_release_permissions(permissions)
        with self.env.cr.savepoint():
            production._lock_quality_transfer_scope()
            for check in permissions.unit_id.run_ids.check_ids.sorted('id'):
                check._lock_unit_admission_scope()
            self.env.flush_all()
            self.env.invalidate_all(flush=False)
            data = production._get_unit_stock_release_proposal(permissions)
            previous = self.search([('production_id', '=', production.id), ('request_key', '=', request_key)])
            if previous:
                if (previous.prepared_by_id != self.env.user
                        or set(previous.permission_ids.ids) != set(permissions.ids)
                        or previous.evidence_snapshot != _snapshot(data)):
                    raise ValidationError(_('Попередній план або його підстави змінилися. Відкрийте новий майстер.'))
                return previous
            plan = self.with_context(**{PLAN_CONTEXT: PLAN_TOKEN}).create({
                'production_id': production.id, 'permission_ids': permissions.ids, 'request_key': request_key,
            })
            production.message_post(body=escape(_(
                'Підготовлено план %(plan)s: %(units)s; запропоновано %(quantity)s шт.; '
                'підготував %(user)s, %(date)s. Складського проведення немає.',
                plan=plan.name, units=', '.join(permissions.unit_id.mapped('name')),
                quantity=plan.quantity, user=self.env.user.display_name, date=plan.prepared_at,
            )), subtype_xmlid='mail.mt_note')
            return plan

    def action_check_current(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        data = self.production_id._get_unit_stock_release_proposal(self.permission_ids)
        if self.evidence_snapshot != _snapshot(data):
            raise ValidationError(_('Підстави, BOM або резерви змінилися. Збережений план не можна використовувати; підготуйте новий.'))
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('План актуальний'), 'message': _(POSTING_UNAVAILABLE), 'type': 'warning', 'sticky': True,
        }}

    def action_post(self):
        """Fail closed; neither a context flag nor sudo enables posting."""
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.production_id._check_unit_stock_release_access()
        self.env['quality.stock.posting']._post_plan(self)
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class MrpProductionStockReleasePlan(models.Model):
    _inherit = 'mrp.production'

    quality_stock_release_plan_ids = fields.One2many(
        'quality.unit.stock.release.plan', 'production_id', string='Плани складського випуску',
        compute='_compute_stock_release_plans', compute_sudo=False, readonly=True, groups='mrp.group_mrp_user',
    )
    can_prepare_unit_stock_release = fields.Boolean(compute='_compute_stock_release_role')

    @api.depends_context('uid')
    def _compute_stock_release_role(self):
        allowed = has_stock_release_roles(self.env.user)
        for production in self:
            production.can_prepare_unit_stock_release = allowed

    @api.depends('quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_stock_release_plans(self):
        allowed = has_stock_release_roles(self.env.user)
        plans = self.env['quality.unit.stock.release.plan']
        for production in self:
            production.quality_stock_release_plan_ids = plans.search([('production_id', '=', production.id)]) if allowed else plans

    @api.model_create_multi
    def create(self, vals_list):
        if any({'quality_stock_release_plan_ids', 'can_prepare_unit_stock_release'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Історію планів встановлює система.'))
        context = dict(self.env.context)
        context.pop('default_quality_stock_release_plan_ids', None)
        context.pop('default_can_prepare_unit_stock_release', None)
        return super(MrpProductionStockReleasePlan, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if {'quality_stock_release_plan_ids', 'can_prepare_unit_stock_release'}.intersection(vals):
            raise UserError(_('Історію планів встановлює система.'))
        return super().write(vals)

    def _check_unit_stock_release_access(self):
        self.ensure_one()
        check_stock_release_roles(self.env.user)
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.quality_transfer_controlled or self.company_id not in self.env.companies:
            raise ValidationError(_('Потрібен захищений MO дозволеної компанії.'))
        for name in ('stock.move', 'stock.move.line'):
            self.env[name].check_access_rights('read')

    def _check_stock_release_permissions(self, permissions):
        self._check_unit_stock_release_access()
        permissions.check_access_rights('read')
        permissions.check_access_rule('read')
        if (not permissions or permissions.production_id != self or permissions.company_id != self.company_id
                or permissions.product_id != self.product_id or len(permissions.unit_id) != len(permissions)):
            raise ValidationError(_('Оберіть різні QUNIT з дозволами тільки цього MO, товару та компанії.'))
        for permission in permissions:
            if permission.unit_id.production_id != self or permission.unit_id.company_id != self.company_id:
                raise ValidationError(_('Ідентифікація QUNIT не відповідає MO та компанії.'))
            error = permission._get_effectiveness_error()
            if error:
                raise ValidationError(_('%(unit)s: %(reason)s', unit=permission.unit_id.name, reason=error))

    def _get_unit_stock_release_proposal(self, permissions):
        """Read-only preflight for a deliberately limited linear BOM case.

        It never calls _post_inventory, _action_done, _split_productions,
        _split, reservation updates, valuation APIs, or manufacturing writes.
        A proposal is not a statement that costing/posting is validated.
        """
        self._check_stock_release_permissions(permissions)
        unit = permissions.unit_id[0]
        all_units = unit._release_records('quality.production.unit', 'production_id = %s', [self.id])
        if any(item.product_id != self.product_id or item.company_id != self.company_id
               or item.registration_run_id.production_id != self for item in all_units):
            raise ValidationError(_('Поіменний склад MO містить несумісні вироби або запуски.'))
        unit_uom = self.env.ref('uom.product_uom_unit')
        if (self.product_id.type != 'product' or self.product_uom_id != self.product_id.uom_id
                or self.product_uom_id.category_id != unit_uom.category_id
                or self.product_uom_id.factor != unit_uom.factor
                or float_compare(self.product_qty, len(all_units), precision_rounding=self.product_uom_id.rounding)):
            raise ValidationError(_('Підготовка потребує складського товару в одиницях: один QUNIT = одна одиниця; план MO має відповідати повному поіменному складу.'))
        if (not self.bom_id or self.bom_id.type != 'normal'
                or self.bom_id.product_tmpl_id != self.product_id.product_tmpl_id
                or self.bom_id.product_id and self.bom_id.product_id != self.product_id
                or self.bom_id.company_id and self.bom_id.company_id != self.company_id):
            raise ValidationError(_('Потрібна однозначна виробнича BOM для перевірки часткового споживання.'))
        moves = unit._release_records('stock.move', 'raw_material_production_id = %s OR production_id = %s', [self.id, self.id])
        moves.check_access_rights('read')
        moves.check_access_rule('read')
        issued = self._check_named_stock_history()
        remaining_units = len(all_units) - len(issued)
        if not moves or remaining_units <= 0 or permissions.unit_id & issued:
            raise ValidationError(_('Вибрані вироби вже випущено або MO не має залишкової партії.'))
        if any(move.company_id != self.company_id for move in moves):
            raise ValidationError(_('Складські рухи іншої компанії не можуть бути частиною плану.'))
        raw = moves.filtered(lambda row: row.raw_material_production_id == self and row.state not in ('done', 'cancel'))
        finished = moves.filtered(lambda row: row.production_id == self and row.state not in ('done', 'cancel'))
        if (not raw or len(finished) != 1 or finished.product_id != self.product_id
                or finished.product_uom != self.product_uom_id or finished.picked
                or finished.product_uom_qty != remaining_units
                or finished.location_dest_id != self.location_dest_id):
            raise ValidationError(_('Потрібен один непроведений рух основного товару на повний план MO без побічної продукції.'))
        move_lines = unit._release_records('stock.move.line', 'move_id IN %s', [tuple(moves.ids)])
        expected = self._get_moves_raw_values()
        expected_by_line = {row['bom_line_id']: row for row in expected}
        if len(expected_by_line) != len(expected) or len(raw) != len(expected):
            raise ValidationError(_('Компоненти не мають однозначної відповідності BOM. Додаткові, об’єднані або змінені рухи потребують явного розподілу.'))
        components, used_bom_lines = [], set()
        try:
            exact_quantity(len(permissions), self.product_uom_id.rounding)
            for move in raw.sorted('id'):
                demand = expected_by_line.get(move.bom_line_id.id)
                if (not demand or move.bom_line_id.id in used_bom_lines
                        or demand['product_id'] != move.product_id.id or move.manual_consumption or demand['manual_consumption']
                        or move.product_id.type != 'product' or move.product_id.tracking != 'none'
                        or move.state != 'assigned' or move.location_id.usage != 'internal'
                        or move.location_dest_id.usage != 'production'):
                    raise ValidationError(_('%s: потрібні однозначна BOM, автоматичне споживання та повний резерв. Ручний, lot/serial або нестандартний розхід потребує окремого явного розподілу.', move.product_id.display_name))
                used_bom_lines.add(move.bom_line_id.id)
                expected_uom = self.env['uom.uom'].browse(demand['product_uom'])
                total = expected_uom._compute_quantity(demand['product_uom_qty'], move.product_uom, round=False)
                remaining_total = proportional_quantity(total, remaining_units, len(all_units), move.product_uom.rounding)
                if float_compare(move.product_uom_qty, remaining_total, precision_rounding=move.product_uom.rounding):
                    raise ValidationError(_('%s: потреба руху відрізняється від BOM; автоматично ділити відхилення або втрати заборонено.', move.product_id.display_name))
                quantity = proportional_quantity(total, len(permissions), len(all_units), move.product_uom.rounding)
                stock_quantity = exact_quantity(move.product_uom._compute_quantity(quantity, move.product_id.uom_id, round=False), move.product_id.uom_id.rounding)
                lines = move_lines.filtered(lambda line: line.move_id == move).sorted('id')
                if not lines or any(
                    line.product_id != move.product_id or line.company_id != self.company_id
                    or line.location_id != move.location_id or line.location_dest_id != move.location_dest_id
                    or line.lot_id or line.lot_name or line.owner_id or line.package_id or line.result_package_id
                    for line in lines
                ):
                    raise ValidationError(_('%s: резерви не відповідають руху або містять партії, власника чи пакування, які потребують явного розподілу.', move.product_id.display_name))
                reserved = [(line.id, line.product_uom_id._compute_quantity(line.quantity, move.product_id.uom_id, round=False)) for line in lines]
                total_stock = move.product_uom._compute_quantity(remaining_total, move.product_id.uom_id, round=False)
                if float_compare(sum(value for _line_id, value in reserved), total_stock, precision_rounding=move.product_id.uom_id.rounding):
                    raise ValidationError(_('%s: кількості stock.move.line не відповідають повному резерву BOM.', move.product_id.display_name))
                slices = []
                for line_id, taken in allocate_reserved_quantity(stock_quantity, reserved, move.product_id.uom_id.rounding):
                    line = lines.browse(line_id)
                    line_quantity = exact_quantity(move.product_id.uom_id._compute_quantity(taken, line.product_uom_id, round=False), line.product_uom_id.rounding)
                    slices.append(dict(line_id=line.id, quantity=line_quantity, uom_id=line.product_uom_id.id, stock_quantity=taken))
                components.append(dict(
                    move_id=move.id, bom_line_id=move.bom_line_id.id, product_id=move.product_id.id,
                    product_name=move.product_id.with_context(lang=False).display_name,
                    uom_id=move.product_uom.id, uom_name=move.product_uom.with_context(lang=False).name,
                    full_quantity=total, quantity=quantity, stock_quantity=stock_quantity,
                    remaining_quantity=exact_quantity(remaining_total - quantity, move.product_uom.rounding),
                    reservations=slices,
                    source_lines=[dict(id=line.id, quantity=line.quantity, uom=line.product_uom_id.id, picked=line.picked) for line in lines],
                    picked=move.picked, source=move.location_id.id, destination=move.location_dest_id.id,
                ))
        except StockReleasePlanError as error:
            raise ValidationError(_('Частковий розхід не можна подати без округлення або зміни резерву: %s', str(error))) from error
        # Cost facts are preserved, but no fabricated unit cost is calculated.
        # Native _cal_price charges all WO time again on each partial posting;
        # the shared/open 13.3 timers require an explicit allocation policy.
        workorders = unit._release_records('mrp.workorder', 'production_id = %s', [self.id])
        timers = self.env['mrp.workcenter.productivity']
        if workorders:
            timers = unit._release_records('mrp.workcenter.productivity', 'workorder_id IN %s', [tuple(workorders.ids)])
        timer_facts = [dict(
            id=timer.id, workorder=timer.workorder_id.id,
            start=fields.Datetime.to_string(timer.date_start), end=fields.Datetime.to_string(timer.date_end),
            # A boolean fact about the accessible timer, not a grant of
            # access to its execution batch and not a cost allocation.
            has_execution_batch=bool(timer.quality_execution_batch_id),
        ) for timer in timers]
        return dict(
            schema=1, posting_available=False, quantity=len(permissions),
            production=dict(id=self.id, company=self.company_id.id, product=self.product_id.id, bom=self.bom_id.id,
                            quantity=self.product_qty, qty_producing=self.qty_producing, uom=self.product_uom_id.id,
                            picking_type=self.picking_type_id.id, tracking=self.product_id.tracking),
            units=[dict(id=permission.unit_id.id, name=permission.unit_id.name, lot=permission.unit_id.lot_id.id,
                        permission=permission.id, check=permission.check_id.id, admission=permission.admission_id.id,
                        evidence=permission.evidence_snapshot) for permission in permissions.sorted('id')],
            components=components,
            released_unit_ids=sorted(issued.ids),
            finished=dict(move=finished.id, quantity=len(permissions), remaining=remaining_units - len(permissions),
                          source=finished.location_id.id, destination=finished.location_dest_id.id,
                          source_lines=[dict(id=line.id, quantity=line.quantity, uom=line.product_uom_id.id,
                                             lot=line.lot_id.id, picked=line.picked)
                                        for line in move_lines.filtered(lambda line: line.move_id == finished).sorted('id')]),
            costing=dict(verified=False, method=self.product_id.with_company(self.company_id).cost_method if 'cost_method' in self.product_id._fields else False,
                         workorders=[dict(id=workorder.id, costs_hour=workorder.workcenter_id.costs_hour) for workorder in workorders],
                         timers=timer_facts, blockers=posting_blocker_codes(timer_facts)),
        )

    def action_open_unit_stock_release_wizard(self):
        self._check_unit_stock_release_access()
        return {'type': 'ir.actions.act_window', 'name': _('Випустити дозволені вироби — підготовка'),
                'res_model': 'quality.unit.stock.release.wizard', 'view_mode': 'form', 'target': 'new',
                'views': [(self.env.ref('quality_vataga.quality_unit_stock_release_wizard_form').id, 'form')],
                'context': {'default_production_id': self.id}}


class QualityReleasePermissionStockPlans(models.Model):
    _inherit = 'quality.unit.release.line'

    stock_release_plan_ids = fields.Many2many(
        'quality.unit.stock.release.plan', 'quality_stock_plan_permission_rel', 'permission_id', 'plan_id',
        string='Підготовлені плани', readonly=True, groups='mrp.group_mrp_user',
    )


class QualityUnitStockReleaseHistory(models.Model):
    _inherit = 'quality.production.unit'

    stock_release_plan_ids = fields.Many2many(
        'quality.unit.stock.release.plan', string='Підготовка складського випуску',
        compute='_compute_stock_release_plans', compute_sudo=False, groups='mrp.group_mrp_user',
    )
    can_prepare_unit_stock_release = fields.Boolean(
        related='production_id.can_prepare_unit_stock_release', compute_sudo=False,
    )

    @api.depends('quality_release_line_ids.stock_release_plan_ids')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_stock_release_plans(self):
        allowed = has_stock_release_roles(self.env.user)
        plans = self.env['quality.unit.stock.release.plan']
        for unit in self:
            unit.stock_release_plan_ids = plans.search([('permission_ids.unit_id', '=', unit.id)]) if allowed else plans
