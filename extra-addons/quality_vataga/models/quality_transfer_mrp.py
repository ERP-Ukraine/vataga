from odoo import _, api, fields, models
from odoo.exceptions import UserError

BOUNDARY_MESSAGE = 'Для цього MO активовано захищений поштучний маршрут. Використайте «Поштучне виконання» з явним вибором прийнятих QUNIT. Стандартне завершення Work Order, зміна виробничої кількості та складський випуск заблоковані.'
TIMER_CLOSE_TOKEN = object()


class MrpProductionTransfer(models.Model):
    _inherit = 'mrp.production'

    quality_transfer_controlled = fields.Boolean(string='Захищений поштучний маршрут', readonly=True, copy=False)
    quality_transfer_revision = fields.Integer(readonly=True, copy=False)

    def _lock_quality_transfer_scope(self):
        """Serialize native execution and first handoff activation on the MO.

        A revision write, including on an ordinary MO, prevents a waiting
        REPEATABLE READ transaction from using a pre-activation snapshot.
        It changes no manufacturing quantity or state. Native calls retain
        their normal ACLs; this private lock confers no extra access.
        """
        if not self:
            return False
        self.check_access_rights('read')
        self.check_access_rule('read')
        self.flush_recordset(['quality_transfer_controlled', 'quality_transfer_revision'])
        controlled = False
        for production in self.sorted('id'):
            self.env.cr.execute('UPDATE mrp_production SET quality_transfer_revision = COALESCE(quality_transfer_revision, 0) + 1 WHERE id = %s RETURNING quality_transfer_controlled', [production.id])
            controlled = bool(self.env.cr.fetchone()[0]) or controlled
        self.invalidate_recordset(['quality_transfer_controlled', 'quality_transfer_revision'], flush=False)
        self.modified(['quality_transfer_revision'])
        return controlled

    def _check_quality_transfer_boundary(self):
        if self._lock_quality_transfer_scope():
            raise UserError(_(BOUNDARY_MESSAGE))

    def _enable_quality_transfer_control(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if not self.quality_transfer_controlled:
            # There is deliberately no public switch to unlock the MO. A
            # returned receipt must not enable an unverified release path.
            super(MrpProductionTransfer, self).write({'quality_transfer_controlled': True})

    @api.model_create_multi
    def create(self, vals_list):
        if any({'quality_transfer_controlled', 'quality_transfer_revision'}.intersection(vals) for vals in vals_list):
            raise UserError(_('Службові поля маршруту передач встановлює система.'))
        context = dict(self.env.context)
        context.pop('default_quality_transfer_controlled', None)
        context.pop('default_quality_transfer_revision', None)
        return super(MrpProductionTransfer, self.with_context(context)).create(vals_list)

    def write(self, vals):
        if vals.get('state') == 'done':
            self._check_matrix_native_completion()
        if {'quality_transfer_controlled', 'quality_transfer_revision'}.intersection(vals):
            raise UserError(_('Службові поля маршруту передач захищені.'))
        if {
            'state', 'qty_producing', 'qty_produced', 'product_qty', 'product_uom_id',
            'product_id', 'company_id', 'bom_id', 'workorder_ids', 'move_raw_ids',
            'move_finished_ids', 'lot_producing_id', 'backorder_sequence', 'allow_workorder_dependencies',
        }.intersection(vals):
            self._check_quality_transfer_boundary()
        return super().write(vals)

    def unlink(self):
        self._check_quality_transfer_boundary()
        return super().unlink()

    def pre_button_mark_done(self):
        self._check_matrix_native_completion()
        self._check_quality_transfer_boundary()
        return super().pre_button_mark_done()

    def button_mark_done(self):
        self._check_matrix_native_completion()
        self._check_quality_transfer_boundary()
        return super().button_mark_done()

    def _post_inventory(self, cancel_backorder=False):
        self._check_matrix_native_completion()
        self._check_quality_transfer_boundary()
        return super()._post_inventory(cancel_backorder=cancel_backorder)

    def _split_productions(self, amounts=False, cancel_remaining_qty=False, set_consumed_qty=False):
        self._check_matrix_native_completion()
        self._check_quality_transfer_boundary()
        return super()._split_productions(amounts=amounts, cancel_remaining_qty=cancel_remaining_qty, set_consumed_qty=set_consumed_qty)


class MrpWorkorderTransferBoundary(models.Model):
    _inherit = 'mrp.workorder'

    def _quality_transfer_relation_productions(self, vals):
        productions = self.production_id | self.env['mrp.production'].browse(vals.get('production_id'))
        if not {'blocked_by_workorder_ids', 'needed_by_workorder_ids'}.intersection(vals):
            return productions
        # Changes through the inverse relation of an ordinary WO must also
        # protect a controlled neighbour, including a hidden neighbour.
        related_ids = set()
        if self.ids:
            self.flush_recordset(['blocked_by_workorder_ids', 'needed_by_workorder_ids'])
            self.env.cr.execute('''
                SELECT workorder_id FROM mrp_workorder_dependencies_rel WHERE blocked_by_id IN %s
                UNION SELECT blocked_by_id FROM mrp_workorder_dependencies_rel WHERE workorder_id IN %s
            ''', [tuple(self.ids), tuple(self.ids)])
            related_ids.update(row[0] for row in self.env.cr.fetchall())
        for name in ('blocked_by_workorder_ids', 'needed_by_workorder_ids'):
            for command in vals.get(name) or []:
                if command[0] in (1, 2, 3, 4):
                    related_ids.add(command[1])
                elif command[0] == 6:
                    related_ids.update(command[2])
        if related_ids:
            self.env.cr.execute('SELECT DISTINCT production_id FROM mrp_workorder WHERE id IN %s', [tuple(related_ids)])
            productions |= self.env['mrp.production'].browse([row[0] for row in self.env.cr.fetchall()])
        return productions

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            defaults = {name: self.env.context.get('default_' + name, []) for name in ('blocked_by_workorder_ids', 'needed_by_workorder_ids')}
            defaults['production_id'] = self.env.context.get('default_production_id')
            self.browse()._quality_transfer_relation_productions(dict(defaults, **vals))._check_quality_transfer_boundary()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('state') == 'done':
            self.production_id._check_matrix_native_completion()
        if {
            'production_id', 'product_id', 'company_id', 'product_uom_id', 'workcenter_id',
            'qty_producing', 'qty_produced', 'qty_reported_from_previous_wo',
            'blocked_by_workorder_ids', 'needed_by_workorder_ids', 'operation_id',
            'move_raw_ids', 'move_finished_ids', 'move_line_ids', 'finished_lot_id', 'time_ids',
        }.intersection(vals) or 'state' in vals:
            self._quality_transfer_relation_productions(vals)._check_quality_transfer_boundary()
        return super().write(vals)

    def unlink(self):
        self._quality_transfer_relation_productions({'blocked_by_workorder_ids': [], 'needed_by_workorder_ids': []})._check_quality_transfer_boundary()
        return super().unlink()

    def button_start(self):
        self.production_id._check_quality_transfer_boundary()
        return super().button_start()

    def button_finish(self):
        self.production_id._check_matrix_native_completion()
        self.production_id._check_quality_transfer_boundary()
        return super().button_finish()

    def button_done(self):
        self.production_id._check_matrix_native_completion()
        self.production_id._check_quality_transfer_boundary()
        return super().button_done()

    def action_mark_as_done(self):
        self.production_id._check_matrix_native_completion()
        self.production_id._check_quality_transfer_boundary()
        return super().action_mark_as_done()

    def _update_finished_move(self):
        self.production_id._check_quality_transfer_boundary()
        return super()._update_finished_move()

    def _begin_quality_execution(self, batch):
        """Explicit unit execution, without native quantity or stock updates."""
        self.ensure_one()
        self._check_quality_execution_access()
        batch.check_access_rights('read')
        batch.check_access_rule('read')
        if batch.workorder_id != self or batch.operator_id != self.env.user or not batch.execution_ids or batch.timer_ids:
            raise UserError(_('Некоректний робочий інтервал.'))
        for record in batch.execution_ids:
            if record.state != 'progress':
                raise UserError(_('Обробка вже завершена.'))
            error = record.transfer_id._get_execution_error(self, current=record)
            if error:
                raise UserError(error)
        timers = self.env['mrp.workcenter.productivity']
        timers.check_access_rights('create')
        values = self._prepare_timeline_vals(self.duration, batch.started_at)
        values.update(quality_execution_batch_id=batch.id, company_id=self.company_id.id,
                      workorder_id=self.id, workcenter_id=self.workcenter_id.id,
                      user_id=self.env.uid, date_start=batch.started_at, date_end=False)
        # Skip only this module's blanket boundary after unit validation,
        # retaining normal ACLs and native productivity constraints.
        context = {key: value for key, value in self.env.context.items() if not key.startswith('default_')}
        super(MrpProductivityTransferBoundary, timers.with_context(context)).create(values)
        values = {'state': 'progress'}
        if not self.date_start:
            values['date_start'] = batch.started_at
        super(MrpWorkorderTransferBoundary, self).write(values)


class StockMoveTransferBoundary(models.Model):
    _inherit = 'stock.move'

    def _quality_transfer_productions(self, vals=None):
        vals = vals or {}
        productions = self.production_id | self.raw_material_production_id | self.workorder_id.production_id
        for name in ('production_id', 'raw_material_production_id'):
            productions |= self.env['mrp.production'].browse(vals.get(name))
        productions |= self.env['mrp.workorder'].browse(vals.get('workorder_id')).production_id
        return productions

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            defaults = {name: self.env.context.get('default_' + name) for name in ('production_id', 'raw_material_production_id', 'workorder_id')}
            if vals.get('state') == 'done':
                self.browse()._quality_transfer_productions(dict(defaults, **vals))._check_matrix_native_completion()
            self.browse()._quality_transfer_productions(dict(defaults, **vals))._check_quality_transfer_boundary()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('state') == 'done':
            self._quality_transfer_productions(vals)._check_matrix_native_completion()
        if {
            'quantity', 'product_uom_qty', 'product_uom', 'move_line_ids', 'picked',
            'production_id', 'raw_material_production_id', 'workorder_id', 'product_id',
            'location_id', 'location_dest_id',
        }.intersection(vals) or vals.get('state') in ('done', 'cancel'):
            self._quality_transfer_productions(vals)._check_quality_transfer_boundary()
        return super().write(vals)

    def unlink(self):
        self._quality_transfer_productions()._check_quality_transfer_boundary()
        return super().unlink()

    def _action_done(self, cancel_backorder=False):
        self._quality_transfer_productions()._check_matrix_native_completion()
        self._quality_transfer_productions()._check_quality_transfer_boundary()
        return super()._action_done(cancel_backorder=cancel_backorder)


class StockMoveLineTransferBoundary(models.Model):
    _inherit = 'stock.move.line'

    def _quality_transfer_productions(self, vals=None):
        vals = vals or {}
        moves = self.move_id | self.env['stock.move'].browse(vals.get('move_id'))
        return moves._quality_transfer_productions(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            defaults = {name: self.env.context.get('default_' + name) for name in ('move_id', 'production_id', 'workorder_id')}
            self.browse()._quality_transfer_productions(dict(defaults, **vals))._check_quality_transfer_boundary()
        return super().create(vals_list)

    def write(self, vals):
        # Include both old and new parent moves so detaching a line cannot
        # evade the boundary. Even a direct ORM quantity write is protected.
        self._quality_transfer_productions(vals)._check_quality_transfer_boundary()
        return super().write(vals)

    def unlink(self):
        self._quality_transfer_productions()._check_quality_transfer_boundary()
        return super().unlink()


class StockScrapTransferBoundary(models.Model):
    _inherit = 'stock.scrap'

    def do_scrap(self):
        (self.production_id | self.workorder_id.production_id)._check_matrix_native_completion()
        (self.production_id | self.workorder_id.production_id)._check_quality_transfer_boundary()
        return super().do_scrap()


class MrpProductivityTransferBoundary(models.Model):
    _inherit = 'mrp.workcenter.productivity'

    quality_execution_batch_id = fields.Many2one('quality.unit.execution.batch', readonly=True, ondelete='restrict', index=True, copy=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            workorder = self.env['mrp.workorder'].browse(vals.get('workorder_id', self.env.context.get('default_workorder_id')))
            stopping = (
                self.env.context.get('quality_vataga_timer_close') is TIMER_CLOSE_TOKEN
                and workorder.id in self.env.context.get('quality_vataga_closing_workorders', ())
                and vals.get('date_end')
            )
            if (vals.get('quality_execution_batch_id') or self.env.context.get('default_quality_execution_batch_id')) and not stopping:
                raise UserError(_('Робочий інтервал QUNIT встановлює система.'))
            if not stopping:
                workorder.production_id._check_quality_transfer_boundary()
        return super().create(vals_list)

    def write(self, vals):
        if 'quality_execution_batch_id' in vals:
            raise UserError(_('Зв’язок робочого часу з виконанням незмінний.'))
        # An already running source timer may be stopped normally. Creating
        # or reassigning a timer must not become an alternate start path.
        stopping = (
            self.env.context.get('quality_vataga_timer_close') is TIMER_CLOSE_TOKEN
            and not vals.get('workorder_id')
            and all(timer.workorder_id.id in self.env.context.get('quality_vataga_closing_workorders', ()) and (timer.date_end or vals.get('date_end')) for timer in self)
        )
        if not stopping:
            workorders = self.workorder_id | self.env['mrp.workorder'].browse(vals.get('workorder_id'))
            workorders.production_id._check_quality_transfer_boundary()
        return super().write(vals)

    def _close(self):
        # Native _close may split an existing timer into closed productive
        # and performance intervals. Permit only that private operation, with
        # normal ACLs, so an active source timer can always be stopped.
        if self.quality_execution_batch_id.execution_ids.filtered(lambda row: row.state == 'progress'):
            raise UserError(_('Активні QUNIT потрібно завершити або зупинити через «Поштучне виконання».'))
        return super(MrpProductivityTransferBoundary, self.with_context(
            quality_vataga_timer_close=TIMER_CLOSE_TOKEN,
            quality_vataga_closing_workorders=tuple(self.workorder_id.ids),
        ))._close()

    def unlink(self):
        self.workorder_id.production_id._check_quality_transfer_boundary()
        return super().unlink()


class QualityCheckTransferIdentityBoundary(models.Model):
    _inherit = 'quality.check'

    def _identify_samples_locked(self, mode, mapping, run, workorder, confirmed):
        # Downstream registration would otherwise let an unknown Shop Floor
        # path treat receipt as processing. Do not create a second QUNIT or
        # resize the frozen batch to simulate partial execution.
        if not self.operation_run_id and not self._is_execution_identification(mode, mapping, run, workorder):
            self._identity_production()._check_quality_transfer_boundary()
        return super()._identify_samples_locked(mode, mapping, run, workorder, confirmed)
