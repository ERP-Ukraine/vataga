"""C-1 B: explicit assignments, separate ordinary actors, unchanged critical board."""
from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError

from .quality_alert import APPROVAL_GROUP
from .production_residual_stock import case_context, case_internal


MASTER = 'quality_vataga.group_quality_production_master'
EXECUTOR = 'quality_vataga.group_quality_production_executor'
COORDINATOR = 'quality.group_quality_manager'
CONTROLLED = {'production_route', 'production_current_decision_id', 'production_decision_ids'}
ASSIGNMENTS = {'production_master_id', 'production_executor_id', 'approval_user_id'}


class ProductionCaseExistingAccess(models.Model):
    _inherit = 'res.users'

    def _production_case_legacy_access(self, model_name, operation='read'):
        """Preserve every existing ACL grant, including ERP-UA custom groups.

        Read and write grants stay distinct: an old read-only role must not
        turn the new master's scoped write grant into a company-wide grant.
        The surrounding ir.rule cache handles role/ACL invalidation.
        """
        self.ensure_one()
        if operation not in ('read', 'write'):
            raise ValueError(operation)
        roles = self.env.ref(MASTER) | self.env.ref(EXECUTOR)
        return bool(self.env['ir.model.access'].sudo().search_count([
            ('active', '=', True), ('model_id.model', '=', model_name), ('perm_' + operation, '=', True),
            '|', ('group_id', '=', False),
            '&', ('group_id', 'in', self.sudo().groups_id.ids), ('group_id', 'not in', roles.ids),
        ], limit=1))


class ProductionCaseNavigation(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _configure_production_case_navigation(self):
        """Reuse existing Alert navigation without granting Quality User itself."""
        if not self.env.su:
            raise AccessError(_('Навігацію налаштовує лише оновлення модуля.'))
        roles = self.env.ref(MASTER) | self.env.ref(EXECUTOR)
        actions = self.env['ir.actions.act_window'].search([('res_model', '=', 'quality.alert')])
        restricted_actions = actions.filtered('groups_id')
        restricted_actions.write({'groups_id': [Command.link(role.id) for role in roles]})
        menus = self.search([('action', 'in', ['ir.actions.act_window,%s' % action.id for action in actions])])
        parents = menus.parent_id
        while parents:
            menus |= parents
            parents = parents.parent_id
        # An unrestricted menu must stay unrestricted for its existing users.
        menus.filtered('groups_id').write({'groups_id': [Command.link(role.id) for role in roles]})


class ProductionCaseDecision(models.Model):
    _name = 'quality.production.case.decision'
    _description = 'Незмінна історія рішення виробничого випадку'
    _order = 'id desc'
    _rec_name = 'alert_id'
    _check_company_auto = True

    alert_id = fields.Many2one('quality.alert', required=True, readonly=True, check_company=True, ondelete='restrict')
    company_id = fields.Many2one(related='alert_id.company_id', store=True)
    disposition_id = fields.Many2one('quality.production.disposition', readonly=True, check_company=True, ondelete='restrict')
    event = fields.Selection([('assigned', 'Призначення'), ('confirmed', 'Рішення'),
                              ('revoked', 'Відкликання'), ('escalated', 'Передача техраді')], required=True, readonly=True)
    route = fields.Selection([('unclassified', 'Не класифіковано'), ('ordinary', 'Звичайний брак'),
                              ('board', 'Технічна рада')], required=True, readonly=True)
    decision_type = fields.Selection([('scrap', 'Списання'), ('unbuild', 'Заявка D')], readonly=True)
    master_id = fields.Many2one('res.users', readonly=True)
    executor_id = fields.Many2one('res.users', readonly=True)
    actor_id = fields.Many2one('res.users', required=True, readonly=True)
    recorded_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    reason = fields.Text(required=True, readonly=True)
    scope = fields.Json(readonly=True)
    supersedes_id = fields.Many2one('quality.production.case.decision', readonly=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if not case_internal(self.env):
            raise AccessError(_('Історію рішення створює лише перевірена дія випадку C.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Зафіксовані рішення та події незмінні.'))

    @api.ondelete(at_uninstall=False)
    def _keep_history(self):
        raise UserError(_('Історія рішень випадку C не видаляється.'))


class ProductionResidualDecision(models.Model):
    _inherit = 'quality.production.residual.operation'

    decision_id = fields.Many2one('quality.production.case.decision', readonly=True, check_company=True, ondelete='restrict')

    @api.model_create_multi
    def create(self, vals_list):
        if case_internal(self.env):
            vals_list = [dict(vals, decision_id=self.env['quality.alert'].browse(vals['alert_id']).production_current_decision_id.id)
                         for vals in vals_list]
        return super().create(vals_list)


class ProductionCaseAuthority(models.Model):
    _inherit = 'quality.alert'

    production_route = fields.Selection([('unclassified', 'Не класифіковано'), ('ordinary', 'Звичайний брак'),
                                         ('board', 'Технічна рада')], default='unclassified', readonly=True, copy=False)
    production_master_id = fields.Many2one('res.users', string='Призначений майстер', copy=False)
    production_executor_id = fields.Many2one('res.users', string='Складський виконавець', copy=False)
    production_assignment_reason = fields.Text(string='Підстава призначення / відкликання', copy=False)
    production_current_decision_id = fields.Many2one('quality.production.case.decision', readonly=True, copy=False, ondelete='restrict')
    production_decision_ids = fields.One2many('quality.production.case.decision', 'alert_id', readonly=True)
    production_case_can_confirm = fields.Boolean(compute='_compute_authority')
    production_case_can_assign = fields.Boolean(compute='_compute_authority')
    production_case_can_escalate = fields.Boolean(compute='_compute_authority')
    production_case_can_revoke = fields.Boolean(compute='_compute_authority')

    def _authority_actor(self, kind):
        self.ensure_one()
        case = self.sudo()
        user = self.env.user
        if (not case.production_case_mo_id or not user.active or user.share
                or case.company_id not in self.env.companies):
            raise AccessError(_('Немає доступу до виробничого випадку цієї компанії.'))
        if kind == 'coordinator':
            allowed = user.has_group(COORDINATOR)
        elif kind == 'master':
            allowed = user.has_group(MASTER) and case.production_master_id == user
        elif kind == 'executor':
            decision = case.production_current_decision_id
            allowed = (user.has_group(EXECUTOR) and user.has_group('stock.group_stock_user')
                       and case.production_executor_id == user and decision.executor_id == user
                       and case.production_master_id != user and decision.master_id != user)
        else:
            allowed = user.has_group(APPROVAL_GROUP) and case.approval_user_id == user
        if not allowed:
            raise AccessError(_('Дія доступна лише призначеному учаснику з потрібною роллю.'))

    def _authority_lock(self, kind):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        self._authority_actor(kind)
        production = self.sudo().production_case_mo_id
        production.write({'write_date': fields.Datetime.now()})
        self.flush_recordset()
        self.env.cr.execute('SELECT id FROM quality_alert WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset()
        case = self.sudo()
        if (not production.quality_production_case_enabled or case != production.quality_production_case_ids
                or case.incoming_hold_id or case.check_id.incoming_hold_id
                or case.picking_id.picking_type_id.code == 'incoming'
                or case.check_id.picking_id.picking_type_id.code == 'incoming'):
            raise UserError(_('Потрібне канонічне сповіщення виробничого випадку C.'))
        self.check_access_rule('read')
        self._authority_actor(kind)

    def _authority_assignments(self, *, require_executor=False):
        case = self.sudo()
        for user, group in ((case.production_master_id, MASTER), (case.production_executor_id, EXECUTOR)):
            if not user and group == EXECUTOR and not require_executor:
                continue
            if (not user or not user.active or user.share or not user.has_group(group)
                    or case.company_id not in user.company_ids):
                raise UserError(_('Призначте активного внутрішнього учасника з потрібною роллю та компанією.'))
        if (case.production_executor_id and (case.production_master_id == case.production_executor_id
                or not case.production_executor_id.has_group('stock.group_stock_user'))):
            raise UserError(_('Майстер і складський виконавець мають бути різними; виконавцю потрібен Stock User.'))

    def _authority_event(self, event, reason, *, scope=False, supersedes=False):
        case = self.sudo()
        return self.env['quality.production.case.decision'].sudo().with_context(case_context(self.env)).create({
            'alert_id': self.id, 'disposition_id': case.production_disposition_id.id,
            'event': event, 'route': case.production_route, 'decision_type': case.decision_type,
            'master_id': case.production_master_id.id,
            'executor_id': case.approval_user_id.id if case.production_route == 'board' else case.production_executor_id.id,
            'actor_id': self.env.uid, 'reason': reason, 'scope': scope, 'supersedes_id': supersedes,
        })

    def action_confirm_ordinary_production(self):
        self._authority_lock('master')
        case = self.sudo()
        if case.production_route == 'board':
            raise UserError(_('Переданий техраді випадок не можна обробити як звичайний.'))
        if case.production_current_decision_id:
            return True
        if case.is_approved or case.production_d_requested_at or case.production_residual_operation_ids:
            raise UserError(_('Цей випадок уже обробляється іншим рішенням.'))
        self._authority_assignments(require_executor=True)
        if case.decision_type not in ('scrap', 'unbuild') or not (case.decision_reason or '').strip():
            raise UserError(_('Вкажіть рішення та причину звичайного браку.'))
        scope = case._production_residual_scope()
        case.with_context(case_context(self.env)).write({'production_route': 'ordinary'})
        decision = self._authority_event('confirmed', case.decision_reason, scope=scope)
        case.with_context(case_context(self.env)).write({
            'production_current_decision_id': decision.id, 'production_residual_approval': scope})
        self._authority_note(_('Звичайне рішення підтвердив майстер. Очікує окремого складського виконавця.'))
        return True

    def action_execute_ordinary_production(self):
        self._authority_lock('executor')
        case = self.sudo()
        decision = case.production_current_decision_id
        if case.production_route != 'ordinary' or decision.event != 'confirmed' or decision.route != 'ordinary':
            raise UserError(_('Немає підтвердженого звичайного рішення майстра.'))
        if (case.production_master_id != decision.master_id or case.production_executor_id != decision.executor_id
                or case.decision_type != decision.decision_type):
            raise UserError(_('Призначення або рішення змінилося після підтвердження.'))
        self._authority_assignments(require_executor=True)
        if case.production_residual_operation_ids or case.production_d_requested_at:
            return True
        if case._production_residual_scope() != decision.scope or case.production_residual_approval != decision.scope:
            raise UserError(_('Фактичний залишок не відповідає рішенню майстра.'))
        return self._execute_production_residual()

    def action_revoke_ordinary_production(self):
        self._authority_lock('coordinator')
        case = self.sudo()
        if (case.production_route != 'ordinary' or not case.production_current_decision_id
                or case.production_residual_operation_ids or case.production_d_requested_at):
            raise UserError(_('Відкликати можна лише невиконане звичайне рішення.'))
        if not (case.production_assignment_reason or '').strip():
            raise UserError(_('Вкажіть причину відкликання перед новим призначенням.'))
        decision = case.production_current_decision_id
        self._authority_event('revoked', case.production_assignment_reason, scope=decision.scope, supersedes=decision.id)
        case.with_context(case_context(self.env)).write({
            'production_current_decision_id': False, 'production_residual_approval': False})
        return True

    def _authority_note(self, body):
        # Executor has scoped read access, not generic Alert.write. Authority was
        # checked by the public action; only the audit message uses elevated metadata.
        self.sudo().message_post(body=body, author_id=self.env.user.partner_id.id)

    def action_send_for_board_approval(self):
        if not self._case_production():
            return super().action_send_for_board_approval()
        self._authority_lock('master')
        case = self.sudo()
        if case.production_current_decision_id:
            raise UserError(_('Звичайний маршрут не можна підмінити дією техради.'))
        if not (case.decision_reason or '').strip():
            raise UserError(_('Вкажіть причину передачі критичного випадку техраді.'))
        self._authority_assignments()
        case.with_context(case_context(self.env)).write({'production_route': 'board'})
        result = super().action_send_for_board_approval()
        self._authority_event('escalated', case.decision_reason, scope=case.production_residual_approval)
        return result

    def action_approve_technical_board(self):
        if not self._case_production():
            return super().action_approve_technical_board()
        self._authority_lock('board')
        if self.sudo().production_route != 'board':
            raise UserError(_('Техрада затверджує лише явно переданий критичний випадок.'))
        result = super().action_approve_technical_board()
        if not self.sudo().production_current_decision_id:
            decision = self._authority_event('confirmed', self.decision_reason, scope=self.production_residual_approval)
            self.sudo().with_context(case_context(self.env)).write({'production_current_decision_id': decision.id})
        return result

    def action_execute_board_quarantine(self):
        if self._case_production():
            self._authority_lock('board')
            if self.sudo().production_route != 'board' or not self.sudo().production_current_decision_id:
                raise UserError(_('Виконання техради недоступне для звичайного рішення.'))
        return super().action_execute_board_quarantine()

    def _execute_production_residual(self):
        # Metadata proof may be read elevated after authority checks; Stock
        # posting stays in the original executor's environment in the shared helper.
        if self.sudo().production_route == 'ordinary':
            self._authority_actor('executor')
        else:
            self._authority_actor('board')
        return super()._execute_production_residual()

    @api.depends_context('uid')
    @api.depends('production_master_id', 'production_executor_id', 'production_route', 'production_current_decision_id',
                 'approval_user_id', 'production_residual_operation_ids', 'production_d_requested_at', 'stage_id')
    def _compute_authority(self):
        for case in self:
            open_case = bool(case.production_case_mo_id and not case.production_residual_operation_ids and not case.production_d_requested_at)
            draft = open_case and not case.production_current_decision_id and case.production_route != 'board'
            master = self.env.user.has_group(MASTER) and case.production_master_id == self.env.user
            case.production_case_can_confirm = draft and master
            case.production_case_can_assign = draft and self.env.user.has_group(COORDINATOR)
            case.production_case_can_escalate = draft and master
            case.production_case_can_revoke = open_case and case.production_route == 'ordinary' and bool(case.production_current_decision_id) and self.env.user.has_group(COORDINATOR)

    @api.depends_context('uid')
    @api.depends('production_route', 'production_master_id', 'production_executor_id', 'production_current_decision_id', 'approval_user_id')
    def _compute_case_can_execute(self):
        for case in self:
            if case.production_route == 'ordinary':
                case.production_case_can_execute = bool(case.production_current_decision_id
                    and case.production_executor_id == self.env.user and case.production_master_id != self.env.user
                    and self.env.user.has_group(EXECUTOR) and self.env.user.has_group('stock.group_stock_user'))
            else:
                case.production_case_can_execute = (case.production_route == 'board' and case.approval_user_id == self.env.user
                    and self.env.user.has_group(APPROVAL_GROUP) and self.env.user.has_group('stock.group_stock_user'))

    @api.depends('production_case_mo_id.quality_disposition_ids.remaining_qty',
                 'production_case_mo_id.quality_disposition_ids.released_qty',
                 'production_case_mo_id.move_finished_ids.state', 'production_case_mo_id.move_finished_ids.quantity',
                 'production_residual_operation_ids.quantity', 'stage_id', 'is_approved', 'production_d_requested_at',
                 'production_route', 'production_current_decision_id')
    def _compute_case_status(self):
        super()._compute_case_status()
        for case in self:
            if case.production_route == 'ordinary' and case.production_current_decision_id and case.production_case_ready:
                case.production_case_status = 'execution'

    @api.model_create_multi
    def create(self, vals_list):
        if not case_internal(self.env):
            names = CONTROLLED | (ASSIGNMENTS - {'approval_user_id'})
            if any(any(vals.get(name) and not (name == 'production_route' and vals[name] == 'unclassified')
                       for name in names) for vals in vals_list):
                raise AccessError(_('Маршрут та призначення C встановлює координатор у канонічному випадку.'))
            vals_list = [dict(vals, production_route='unclassified', production_master_id=False,
                              production_executor_id=False, production_current_decision_id=False, production_decision_ids=[])
                         for vals in vals_list]
        return super().create(vals_list)

    def write(self, vals):
        if not case_internal(self.env):
            if CONTROLLED & vals.keys():
                raise AccessError(_('Маршрут і підтверджене рішення змінюються лише перевіреними діями C.'))
            for case in self.filtered('production_case_mo_id'):
                if ASSIGNMENTS & vals.keys():
                    case._authority_lock('coordinator')
                    if (case.production_current_decision_id or case.production_route == 'board'
                            or case.is_approved or case.production_residual_operation_ids or case.production_d_requested_at):
                        raise UserError(_('Спершу відкличте невиконане звичайне рішення; критичний маршрут незмінний.'))
                    if not (vals.get('production_assignment_reason', case.production_assignment_reason) or '').strip():
                        raise UserError(_('Вкажіть підставу призначення.'))
                elif 'production_assignment_reason' in vals:
                    case._authority_lock('coordinator')
                if {'decision_type', 'decision_reason', 'production_case_proposal'} & vals.keys():
                    if case.production_current_decision_id:
                        raise UserError(_('Підтверджене рішення незмінне.'))
                    if not self.env.user.has_group(COORDINATOR):
                        case._authority_lock('master')
                if 'picking_id' in vals and case.picking_id.id != (vals['picking_id'] or False):
                    raise AccessError(_('Виробничий випадок не можна підмінити складським надходженням.'))
        result = super().write(vals)
        if not case_internal(self.env) and ASSIGNMENTS & vals.keys():
            for case in self.filtered('production_case_mo_id'):
                case._authority_assignments()
                approver = case.sudo().approval_user_id
                if approver and (not approver.active or approver.share or not approver.has_group(APPROVAL_GROUP)
                                 or case.company_id not in approver.company_ids):
                    raise UserError(_('Призначений затверджуючий має бути активним членом техради цієї компанії.'))
                case._authority_event('assigned', case.production_assignment_reason)
        return result
