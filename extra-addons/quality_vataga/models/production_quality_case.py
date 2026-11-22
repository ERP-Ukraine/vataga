"""C1: one opt-in manufacturing case; existing board authority; exact residual."""
import json
from collections import defaultdict

from markupsafe import Markup
from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError

from .quality_alert import APPROVAL_GROUP, BOARD_STAGE_XMLIDS
from .production_residual_plan import residual_scope
from .production_residual_stock import (case_context, case_internal, check_residual_stock,
                                        complete_residual_scrap)


CASE_AUDIT = {'production_case_mo_id', 'production_case_check_ids', 'production_case_evidence',
              'production_residual_approval', 'production_proposed_by_id', 'production_proposed_at',
              'production_d_requested_by_id', 'production_d_requested_at'}
CASE_READ_GROUPS = ('quality.group_quality_user,quality_vataga.group_quality_production_master,'
                    'quality_vataga.group_quality_production_executor')


class ProductProductionCase(models.Model):
    _inherit = 'product.template'

    quality_production_case_control = fields.Boolean(
        string='Обробка виробничого браку (етап C)', copy=False,
        groups='quality.group_quality_manager,stock.group_stock_manager',
        help='Лише нові МО CAB з B. Звичайний брак: майстер → окремий склад. Критичний: техрада.')


class ProductionQualityCase(models.Model):
    _inherit = 'mrp.production'

    quality_production_case_enabled = fields.Boolean(readonly=True, copy=False)
    quality_production_case_ids = fields.One2many('quality.alert', 'production_case_mo_id', readonly=True,
                                                 groups=CASE_READ_GROUPS)

    @api.model_create_multi
    def create(self, vals_list):
        productions = super().create([dict(vals, quality_production_case_enabled=False) for vals in vals_list])
        for production in productions:
            if (production.quality_partial_release_enabled
                    and production.product_id.sudo().quality_production_case_control
                    and production.picking_type_id.warehouse_id.code == 'CAB'):
                production.with_context(case_context(self.env)).write({'quality_production_case_enabled': True})
        return productions

    def write(self, vals):
        if 'quality_production_case_enabled' in vals and not case_internal(self.env):
            raise AccessError(_('Етап C фіксується лише для нового явно включеного МО.'))
        return super().write(vals)

    def _quality_release_if_passed(self):
        result = super()._quality_release_if_passed()
        for production in self.filtered('quality_production_case_enabled').sorted('id'):
            production._quality_ensure_production_case()
        return result

    def _quality_ensure_production_case(self):
        self.ensure_one()
        if not self.quality_production_case_enabled:
            return self.env['quality.alert']
        self.write({'write_date': fields.Datetime.now()})
        self.invalidate_recordset()
        checks = self._quality_matrix_checks()
        failures = checks.filtered(lambda check: check.quality_state == 'fail')
        if not failures:
            return self.quality_production_case_ids
        alert = self.quality_production_case_ids
        evidence = dict(alert.production_case_evidence or {}) if alert else {}
        additions = []
        for check in failures.sorted('id'):
            if str(check.id) not in evidence:
                description = check._get_technical_board_failure_description()
                evidence[str(check.id)] = {
                    'check_id': check.id, 'point_id': check.point_id.id,
                    'workorder_id': check.workorder_id.id, 'state': check.quality_state,
                    'lot_id': (check.automatic_sample_lot_id or check.lot_id).id,
                    'registered_pieces': check.automatic_sample_count,
                    'recorded_at': fields.Datetime.to_string(fields.Datetime.now()),
                    'samples': [{'id': sample.id, 'number': sample.sample_number,
                                 'result': sample.sample_result, 'complete': sample.is_complete,
                                 'current': sample._measurement_snapshot(),
                                 'initial_failures': sample.initial_failures}
                                for sample in check.sample_ids],
                    'description': str(description),
                }
                additions.append(Markup('<p>%s — %s</p>') % (check.name, check.workorder_id.display_name or self.name)
                                 + Markup(description))
        model = self.env['quality.alert'].sudo().with_context(case_context(self.env))
        if not alert:
            first = failures.sorted('id')[0]
            values = {
                'name': _('Виробничий брак: %s', self.name), 'production_case_mo_id': self.id,
                'production_case_check_ids': [Command.set(checks.ids)], 'production_case_evidence': evidence,
                'company_id': self.company_id.id, 'product_id': self.product_id.id,
                'lot_id': (first.automatic_sample_lot_id or first.lot_id).id,
                'team_id': first.team_id.id, 'check_id': first.id,
                'description': Markup('<p>%s</p>') % _('Брак утримується. Складська обробка можлива лише за підтвердженим розподілом ВТК.')
                               + Markup('').join(additions),
            }
            if 'product_tmpl_id' in model._fields:
                values['product_tmpl_id'] = self.product_id.product_tmpl_id.id
            alert = model.create(values)
        else:
            values = {}
            if additions:
                values.update(production_case_evidence=evidence,
                              description=Markup(alert.description or '') + Markup('').join(additions))
            if set(alert.production_case_check_ids.ids) != set(checks.ids):
                values['production_case_check_ids'] = [Command.set(checks.ids)]
            if values:
                alert.sudo().with_context(case_context(self.env)).write(values)
        return alert

    def action_open_production_quality_case(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        alert = self.quality_production_case_ids
        if len(alert) != 1:
            raise UserError(_('Пов’язане сповіщення про підтверджений FAIL ще не створено.'))
        return {'type': 'ir.actions.act_window', 'res_model': 'quality.alert', 'res_id': alert.id, 'view_mode': 'form'}


class ProductionCaseCheck(models.Model):
    _inherit = 'quality.check'

    def do_alert(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        if self.production_id.sudo().quality_production_case_enabled:
            if self.quality_state != 'fail':
                raise UserError(_('Сповіщення C створюється після підтвердженого FAIL.'))
            production = self.production_id.sudo()
            production._quality_ensure_production_case()
            return production.action_open_production_quality_case()
        return super().do_alert()


class ProductionResidualOperation(models.Model):
    _name = 'quality.production.residual.operation'
    _description = 'Проведена обробка залишку виробничого браку'
    _rec_name = 'scrap_id'
    _check_company_auto = True

    alert_id = fields.Many2one('quality.alert', required=True, readonly=True, check_company=True, ondelete='restrict')
    disposition_id = fields.Many2one('quality.production.disposition', required=True, readonly=True,
                                     check_company=True, ondelete='restrict')
    company_id = fields.Many2one(related='disposition_id.company_id', store=True)
    source_line_id = fields.Many2one('stock.move.line', required=True, readonly=True, check_company=True, ondelete='restrict')
    quantity = fields.Float(string='Списано, шт.', required=True, readonly=True)
    scrap_id = fields.Many2one('stock.scrap', required=True, readonly=True, check_company=True, ondelete='restrict')
    executed_by_id = fields.Many2one('res.users', required=True, readonly=True)
    executed_at = fields.Datetime(required=True, readonly=True)
    snapshot = fields.Json(required=True, readonly=True)

    _sql_constraints = [('one_residual_source', 'unique(source_line_id)', 'Цей залишок уже оброблено.')]

    @api.model_create_multi
    def create(self, vals_list):
        if not case_internal(self.env):
            raise AccessError(_('Аудит обробки створює лише підтверджене складське виконання.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Проведена обробка та її джерело незмінні.'))

    @api.ondelete(at_uninstall=False)
    def _keep_residual_audit(self):
        raise UserError(_('Історія обробки виробничого браку не видаляється.'))


class ProductionCaseDisposition(models.Model):
    _inherit = 'quality.production.disposition'

    residual_operation_ids = fields.One2many('quality.production.residual.operation', 'disposition_id', readonly=True)
    production_case_ids = fields.One2many(related='production_id.quality_production_case_ids', readonly=True,
                                         groups=CASE_READ_GROUPS)

    @api.depends('produced_qty', 'release_move_ids.state', 'release_move_ids.quantity',
                 'source_move_ids.quality_production_board_alert_ids.board_executed_at',
                 'source_move_ids.quality_production_board_alert_ids.board_quantity', 'residual_operation_ids.quantity')
    def _compute_processing(self):
        super()._compute_processing()
        for disposition in self:
            processed = sum(disposition.sudo().residual_operation_ids.mapped('quantity'))
            disposition.disposed_qty += processed
            disposition.remaining_qty -= processed


class ProductionCaseAlert(models.Model):
    _inherit = 'quality.alert'

    production_case_mo_id = fields.Many2one('mrp.production', readonly=True, copy=False, check_company=True,
                                           ondelete='restrict', string='Виробничий випадок C')
    production_case_check_ids = fields.Many2many(
        'quality.check', 'quality_production_case_check_rel', 'alert_id', 'check_id',
        readonly=True, copy=False, string='Первинні QC')
    production_case_evidence = fields.Json(readonly=True, copy=False)
    production_case_evidence_text = fields.Text(compute='_compute_case_evidence')
    production_residual_approval = fields.Json(readonly=True, copy=False)
    production_case_proposal = fields.Selection([
        ('scrap', 'Списання залишку'), ('unbuild', 'Розбирання — заявка етапу D'),
    ], compute='_compute_case_proposal', inverse='_inverse_case_proposal', string='Пропозиція / рішення')
    production_proposed_by_id = fields.Many2one('res.users', readonly=True, copy=False, string='Передав техраді')
    production_proposed_at = fields.Datetime(readonly=True, copy=False, string='Дата передачі')
    production_d_requested_by_id = fields.Many2one('res.users', readonly=True, copy=False, string='Заявку D підтвердив')
    production_d_requested_at = fields.Datetime(readonly=True, copy=False, string='Дата заявки D')
    production_residual_operation_ids = fields.One2many('quality.production.residual.operation', 'alert_id', readonly=True)
    production_disposition_id = fields.Many2one('quality.production.disposition', compute='_compute_case_status', compute_sudo=True)
    production_case_ready = fields.Boolean(compute='_compute_case_status', compute_sudo=True)
    production_case_can_execute = fields.Boolean(compute='_compute_case_can_execute')
    production_case_reason = fields.Text(compute='_compute_case_status', compute_sudo=True, string='Підстава утримання')
    production_case_held_qty = fields.Float(compute='_compute_case_status', compute_sudo=True, string='У карантині, шт.')
    production_case_disposed_qty = fields.Float(compute='_compute_case_status', compute_sudo=True, string='Списано, шт.')
    production_case_released_qty = fields.Float(compute='_compute_case_status', compute_sudo=True, string='Випущено, шт.')
    production_case_produced_qty = fields.Float(compute='_compute_case_status', compute_sudo=True, string='Виготовлено, шт.')
    production_case_status = fields.Selection([
        ('waiting', 'Утримання / очікує підтвердженого залишку'), ('review', 'Підготовка пропозиції'),
        ('approval', 'На затвердженні техради'), ('execution', 'Очікує окремого виконання'),
        ('waiting_d', 'Заявка D; вироби залишаються в карантині'), ('done', 'Списання залишку завершено'),
    ], compute='_compute_case_status', compute_sudo=True, string='Обробка браку')

    _sql_constraints = [('one_production_case', 'unique(production_case_mo_id)', 'Сповіщення цього виробничого випадку вже існує.')]

    @api.depends('decision_type')
    def _compute_case_proposal(self):
        for alert in self:
            alert.production_case_proposal = alert.decision_type if alert.decision_type in ('scrap', 'unbuild') else False

    def _inverse_case_proposal(self):
        for alert in self:
            if not alert.production_case_mo_id:
                raise UserError(_('Пропозиція C доступна лише для виробничого випадку.'))
            alert.write({'decision_type': alert.production_case_proposal or False})

    @api.depends('production_case_evidence')
    def _compute_case_evidence(self):
        for alert in self:
            alert.production_case_evidence_text = json.dumps(alert.production_case_evidence, ensure_ascii=False, indent=2)

    @api.depends_context('uid')
    @api.depends('approval_user_id')
    def _compute_case_can_execute(self):
        allowed = self.env.user.has_group(APPROVAL_GROUP) and self.env.user.has_group('stock.group_stock_user')
        for alert in self:
            alert.production_case_can_execute = allowed and alert.approval_user_id == self.env.user

    @api.depends('production_case_mo_id.quality_disposition_ids.remaining_qty',
                 'production_case_mo_id.quality_disposition_ids.released_qty',
                 'production_case_mo_id.move_finished_ids.state', 'production_case_mo_id.move_finished_ids.quantity',
                 'production_residual_operation_ids.quantity', 'stage_id', 'is_approved', 'production_d_requested_at')
    def _compute_case_status(self):
        for alert in self:
            disposition = alert.production_case_mo_id.quality_disposition_ids[:1]
            alert.production_disposition_id = disposition
            alert.production_case_produced_qty = disposition.produced_qty
            alert.production_case_released_qty = disposition.released_qty
            alert.production_case_held_qty = disposition.remaining_qty
            alert.production_case_disposed_qty = disposition.disposed_qty
            if alert.production_case_mo_id and not disposition:
                receipts, _checks, sources, _facts = alert.production_case_mo_id._quality_disposition_facts()
                unit = self.env.ref('uom.product_uom_unit')
                produced = sum(source['quantity'] for source in sources)
                released = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False)
                               for move in receipts.quality_release_move_ids if move.state == 'done')
                alert.production_case_produced_qty = produced
                alert.production_case_released_qty = released
                alert.production_case_held_qty = produced - released
            ready, reason = False, False
            if alert.production_case_mo_id:
                try:
                    alert._production_residual_scope()
                    ready = True
                except UserError as error:
                    reason = str(error)
            status = 'review' if ready else 'waiting'
            if alert.is_board_approval_stage and ready:
                status = 'approval'
            if alert.is_approved and ready:
                status = 'execution'
            if alert.production_d_requested_at:
                status, ready, reason = 'waiting_d', False, _('Очікує керованого розбирання етапу D. Рухів компонентів немає.')
            if alert.production_residual_operation_ids:
                status, ready, reason = 'done', False, False
            alert.production_case_ready, alert.production_case_reason = ready, reason
            alert.production_case_status = status

    def _case_production(self):
        self.ensure_one()
        return self.sudo().production_case_mo_id or self.sudo().check_id.production_id.filtered('quality_production_case_enabled')

    def _case_lock(self):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        production = self._case_production()
        if not production or not self.production_case_mo_id or self != production.quality_production_case_ids:
            raise UserError(_('Використовуйте єдине сповіщення виробничого випадку C.'))
        production.with_user(self.env.user).check_access_rights('read')
        production.with_user(self.env.user).check_access_rule('read')
        self.check_id.check_access_rights('read')
        self.check_id.check_access_rule('read')
        production.write({'write_date': fields.Datetime.now()})
        self._lock_board_records()
        return production.with_user(self.env.user)

    def _production_residual_scope(self, *, lock=False):
        self.ensure_one()
        production = self.production_case_mo_id
        disposition = production.quality_disposition_ids
        if not production or production.state != 'done' or len(disposition) != 1:
            raise UserError(_('Очікується завершений розподіл ВТК. Складська обробка заборонена.'))
        if self.production_residual_operation_ids:
            raise UserError(_('Залишок уже оброблено.'))
        receipts, checks, sources, _facts = production._quality_disposition_facts()
        if set(disposition.source_move_ids.ids) != set(receipts.ids) or set(disposition.check_ids.ids) != set(checks.ids):
            raise UserError(_('Первинні джерела або обов’язкові QC змінилися.'))
        released = defaultdict(float)
        unit = self.env.ref('uom.product_uom_unit')
        releases = receipts.quality_release_move_ids
        if set(releases.ids) != set(disposition.release_move_ids.ids) or any(move.state not in ('draft', 'done') for move in releases):
            raise UserError(_('Канонічний випуск не відповідає розподілу.'))
        for move in releases.filtered(lambda row: row.state == 'done'):
            for line in move.move_line_ids.filtered(lambda row: row.quantity > 0):
                matched = [source for source in sources if source['move_id'] == move.quality_held_move_id.id
                           and source['lot_id'] == line.lot_id.id and source['owner_id'] == line.owner_id.id
                           and source['package_id'] == line.package_id.id]
                if len(matched) != 1 or line.location_id != move.quality_held_move_id.location_dest_id:
                    raise UserError(_('Неоднозначні фактичні рядки дозволеного випуску.'))
                released[matched[0]['id']] += line.product_uom_id._compute_quantity(line.quantity, unit, round=False)
        try:
            scopes = residual_scope(disposition.snapshot, sources, released, production.product_tracking)
        except (ValueError, KeyError, TypeError) as error:
            raise UserError(_('Залишок не має однозначного поштучного підтвердження: %s', str(error))) from error
        check_residual_stock(self.env, scopes, lock=lock)
        return scopes

    def _prepare_board_quarantine_approval(self):
        if not self._case_production():
            return super()._prepare_board_quarantine_approval()
        if not self.production_case_mo_id or self.decision_type not in ('scrap', 'unbuild'):
            raise UserError(_('У C доступні списання підтвердженого залишку або заявка розбирання D.'))
        if not (self.decision_reason or '').strip():
            raise UserError(_('Вкажіть підставу пропозиції та передачі технічній раді.'))
        scopes = self.sudo()._production_residual_scope()
        if self.production_residual_approval and self.production_residual_approval != scopes:
            raise UserError(_('Підтверджений залишок змінився після передачі техраді.'))
        return {'production_residual_approval': scopes}

    def action_send_for_board_approval(self):
        if not self._case_production():
            return super().action_send_for_board_approval()
        self._case_lock()
        scope = self._prepare_board_quarantine_approval()
        result = super().action_send_for_board_approval()
        self.with_context(case_context(self.env)).write(dict(scope, production_proposed_by_id=self.env.uid,
                                                           production_proposed_at=fields.Datetime.now()))
        return result

    def action_approve_technical_board(self):
        if not self._case_production():
            return super().action_approve_technical_board()
        self._case_lock()
        return super().action_approve_technical_board()

    def action_execute_board_quarantine(self):
        if not self._case_production():
            return super().action_execute_board_quarantine()
        self._case_lock()
        if (not self.env.user.has_group(APPROVAL_GROUP) or not self.env.user.has_group('stock.group_stock_user')
                or not self.is_approved or self.approval_user_id != self.env.user or self.approved_by_id != self.env.user
                or self.stage_id != self.env.ref(BOARD_STAGE_XMLIDS[2]) or self.company_id not in self.env.companies):
            raise AccessError(_('Виконує лише призначений затверджуючий техради з правами складу після затвердження.'))
        if self.production_residual_operation_ids or self.production_d_requested_at:
            return True
        return self._execute_production_residual()

    def _execute_production_residual(self):
        """Shared private executor; public entry points verify their own authority."""
        self.ensure_one()
        production = self.sudo().production_case_mo_id
        scopes = self.sudo()._production_residual_scope(lock=True)
        if scopes != self.production_residual_approval:
            raise UserError(_('Залишок більше не відповідає затвердженому рішенню.'))
        if self.decision_type == 'unbuild':
            self.sudo().with_context(case_context(self.env)).write({
                'production_d_requested_at': fields.Datetime.now(), 'production_d_requested_by_id': self.env.uid})
            self.sudo().message_post(author_id=self.env.user.partner_id.id,
                                    body=_('Заявку D підтверджено. Вироби залишаються в карантині; розбирання та рухів компонентів немає.'))
            return True
        if self.decision_type != 'scrap':
            raise UserError(_('Це рішення не має складського виконання в C.'))
        scraps = complete_residual_scrap(self.env, scopes, self.display_name, alert_id=self.id)
        for scope, scrap in zip(scopes, scraps):
            # Preserve the existing MRP scrap safety: bind only after posting.
            scrap.with_context(case_context(self.env)).write({'production_id': production.id})
            self.env['quality.production.residual.operation'].sudo().with_context(case_context(self.env)).create({
                'alert_id': self.id, 'disposition_id': production.quality_disposition_ids.id,
                'source_line_id': scope['id'], 'quantity': scope['quantity'], 'scrap_id': scrap.id,
                'executed_by_id': self.env.uid, 'executed_at': fields.Datetime.now(), 'snapshot': scope,
            })
        pending = production.move_finished_ids.quality_release_move_ids.filtered(lambda move: move.state == 'draft')
        if pending:
            pending.with_user(self.env.user).with_context(case_context(self.env))._action_cancel()
        self.sudo().message_post(author_id=self.env.user.partner_id.id,
                                body=_('Списано лише підтверджений залишок: %s шт. Раніше випущені вироби та QC збережено.',
                                sum(scope['quantity'] for scope in scopes)))
        return True

    @api.model_create_multi
    def create(self, vals_list):
        if not case_internal(self.env):
            defaults = self.default_get(['check_id'])
            for vals in vals_list:
                if any(vals.get(key) for key in CASE_AUDIT):
                    raise AccessError(_('Виробничий випадок і його докази створює лише процес підтвердженого FAIL.'))
                check = self.env['quality.check'].browse(vals.get('check_id', defaults.get('check_id')))
                if check.sudo().production_id.quality_production_case_enabled:
                    raise UserError(_('Відкрийте автоматичне сповіщення з QC або МО. Друге сповіщення C не створюється.'))
            vals_list = [dict(vals, **dict.fromkeys(CASE_AUDIT, False)) for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if CASE_AUDIT & vals.keys() and not case_internal(self.env):
            raise AccessError(_('Зв’язки, докази та підтвердження C не редагуються вручну.'))
        for alert in self:
            if (alert.production_case_mo_id and vals.get('decision_type')
                    and vals['decision_type'] not in ('scrap', 'unbuild')):
                raise UserError(_('У C доступні лише списання або заявка розбирання D.'))
            if alert.production_case_mo_id and any(
                    alert[key].id != (vals[key] or False) for key in
                    {'check_id', 'product_id', 'company_id', 'lot_id'} & vals.keys()):
                raise AccessError(_('Первинний виробничий випадок та його партія незмінні.'))
            if 'check_id' in vals:
                check = self.env['quality.check'].sudo().browse(vals['check_id'])
                if check.production_id.quality_production_case_enabled and not alert.production_case_mo_id:
                    raise AccessError(_('Наявне сповіщення не можна підмінити виробничим випадком C.'))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _keep_production_case(self):
        if self.filtered('production_case_mo_id'):
            raise UserError(_('Історія виробничого випадку C не видаляється.'))
