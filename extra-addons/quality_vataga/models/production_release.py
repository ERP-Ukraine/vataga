"""B1: new opt-in MOs, completed matrices, one immutable stock disposition."""
import json
from math import isclose

from odoo import _, api, Command, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .production_release_plan import plan_production_release
from .production_release_stock import complete_partial_production_release


PRODUCTION_DISPOSITION_CONTEXT = 'quality_vataga_production_disposition'
PRODUCTION_DISPOSITION_OPERATION = object()

REASONS = {
    'missing_scope': 'Немає повного фактичного випуску та обов’язкових QCP.',
    'invalid_source': 'Неоднозначна або неціла фактична кількість.',
    'missing_identity': 'Не підтверджено партію або серійний номер.',
    'ambiguous_checks': 'QC дублюються або не відповідають фактичним партіям.',
    'missing_checks': 'Не завершено повний обов’язковий набір QC кожної партії.',
    'ambiguous_serial': 'Серійний номер не відповідає рівно одному виробу.',
    'pending_checks': 'Очікується завершення всіх обов’язкових QC.',
    'invalid_matrix': 'Матриця, кількість зразків або ЗВТ не відповідають фактичному випуску.',
    'pending_samples': 'Є незавершені поштучні перевірки. Випуск заборонено.',
    'inconsistent_pass': 'Загальний PASS суперечить результатам зразків.',
    'global_fail_without_failed_item': 'QC FAIL без однозначного розподілу фізичних виробів.',
    'unit_identity_required': 'Для змішаних результатів кількох QC потрібна стала ідентифікація кожного виробу.',
    'ambiguous_source_lines': 'Годні вироби не прив’язані до конкретних рядків, власників або пакувань.',
    'shared_quarantine_identity': 'Ключ партії у карантині використовувався іншим випуском; частковий вибір неоднозначний.',
    'unrepresentable_quantity': 'Поштучна кількість не представима з точністю складської одиниці.',
}


def disposition_internal(env):
    return env.context.get(PRODUCTION_DISPOSITION_CONTEXT) is PRODUCTION_DISPOSITION_OPERATION


class ProductProductionControl(models.Model):
    _inherit = 'product.template'

    quality_partial_production_control = fields.Boolean(
        string='Поштучний випуск ВТК (етап B)', copy=False,
        groups='quality.group_quality_manager,stock.group_stock_manager',
        help='Лише нові МО цього товару на CAB з виробничим карантином. Існуючі МО не змінюються.')


class ProductionDisposition(models.Model):
    _name = 'quality.production.disposition'
    _description = 'Зафіксований розподіл фактичного випуску ВТК'
    _rec_name = 'production_id'
    _check_company_auto = True

    production_id = fields.Many2one('mrp.production', required=True, readonly=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one(related='production_id.company_id', store=True)
    product_id = fields.Many2one(related='production_id.product_id')
    check_ids = fields.Many2many('quality.check', readonly=True, copy=False)
    source_move_ids = fields.Many2many('stock.move', 'quality_disposition_source_rel', readonly=True, copy=False)
    release_move_ids = fields.Many2many('stock.move', 'quality_disposition_release_rel', readonly=True, copy=False)
    snapshot = fields.Json(required=True, readonly=True, copy=False)
    snapshot_text = fields.Text(compute='_compute_snapshot_text')
    produced_qty = fields.Float(string='Фактично виготовлено, шт.', required=True, readonly=True)
    good_qty = fields.Float(string='Доведено годних, шт.', required=True, readonly=True)
    bad_qty = fields.Float(string='Доведено браку, шт.', required=True, readonly=True)
    unidentified_qty = fields.Float(string='Без поштучного зіставлення, шт.', required=True, readonly=True)
    released_qty = fields.Float(string='Випущено, шт.', compute='_compute_processing')
    disposed_qty = fields.Float(string='Виведено за рішенням техради, шт.', compute='_compute_processing')
    remaining_qty = fields.Float(string='Залишилось у карантині, шт.', compute='_compute_processing')
    reason = fields.Text(string='Підстава утримання', readonly=True)
    decided_by_id = fields.Many2one('res.users', required=True, readonly=True)
    decided_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)

    _sql_constraints = [('one_production_disposition', 'unique(production_id)',
                         'Розподіл цього фактичного випуску вже зафіксовано.')]

    @api.depends('snapshot')
    def _compute_snapshot_text(self):
        for decision in self:
            decision.snapshot_text = json.dumps(decision.snapshot, ensure_ascii=False, indent=2)

    @api.depends('produced_qty', 'release_move_ids.state', 'release_move_ids.quantity',
                 'source_move_ids.quality_production_board_alert_ids.board_executed_at',
                 'source_move_ids.quality_production_board_alert_ids.board_quantity')
    def _compute_processing(self):
        unit = self.env.ref('uom.product_uom_unit')
        for decision in self:
            released = sum(move.product_uom._compute_quantity(move.quantity, unit, round=False)
                           for move in decision.release_move_ids if move.state == 'done')
            alerts = decision.sudo().source_move_ids.quality_production_board_alert_ids.filtered(
                lambda alert: alert.board_executed_at and alert.decision_type in ('scrap', 'unbuild'))
            disposed = sum(alert.product_id.uom_id._compute_quantity(alert.board_quantity, unit, round=False)
                           for alert in alerts)
            decision.released_qty = released
            decision.disposed_qty = disposed
            decision.remaining_qty = decision.produced_qty - released - disposed

    @api.model_create_multi
    def create(self, vals_list):
        if not disposition_internal(self.env):
            raise AccessError(_('Розподіл створює лише процес підтвердженого випуску ВТК.'))
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_('Зафіксований розподіл та початкові результати незмінні.'))

    @api.ondelete(at_uninstall=False)
    def _keep_history(self):
        raise UserError(_('Історія розподілу ВТК не видаляється.'))


class StockMoveProductionDisposition(models.Model):
    _inherit = 'stock.move'

    quality_production_board_alert_ids = fields.One2many('quality.alert', 'board_receipt_id', readonly=True)


class MrpProductionPartial(models.Model):
    _inherit = 'mrp.production'

    quality_partial_release_enabled = fields.Boolean(readonly=True, copy=False)
    quality_partial_release_note = fields.Text(string='Стан поштучного розподілу', readonly=True, copy=False)
    quality_disposition_ids = fields.One2many('quality.production.disposition', 'production_id', readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals, quality_partial_release_enabled=False, quality_partial_release_note=False)
                     for vals in vals_list]
        productions = super().create(vals_list)
        for production in productions:
            if (production.product_id.sudo().quality_partial_production_control
                    and production.picking_type_id.warehouse_id.code == 'CAB'
                    and production._quality_quarantine_location()):
                production.with_context(**{PRODUCTION_DISPOSITION_CONTEXT: PRODUCTION_DISPOSITION_OPERATION}).write({
                    'quality_partial_release_enabled': True})
        return productions

    def write(self, vals):
        if not disposition_internal(self.env) and {
                'quality_partial_release_enabled', 'quality_partial_release_note'} & vals.keys():
            raise AccessError(_('Режим і результат випуску визначаються процесом ВТК для нового МО.'))
        for production in self.filtered('quality_partial_release_enabled'):
            if any(production[key].id != (vals[key] or False)
                   for key in {'product_id', 'picking_type_id', 'company_id'} & vals.keys()):
                raise UserError(_('Товар і джерело поштучного МО зафіксовано. Створіть нове МО для іншого джерела.'))
        return super().write(vals)

    def _quality_release_if_passed(self):
        scoped = self.filtered('quality_partial_release_enabled')
        super(MrpProductionPartial, self - scoped)._quality_release_if_passed()
        for production in scoped.sorted('id'):
            production._quality_apply_disposition()

    def _quality_disposition_facts(self):
        self.ensure_one()
        unit = self.env.ref('uom.product_uom_unit')
        quarantine = self._quality_quarantine_location()
        receipts = self.move_finished_ids.filtered(lambda move: (
            move.state == 'done' and move.product_id == self.product_id and not move.byproduct_id
            and move.location_dest_id == quarantine and move.quantity > 0))
        lines = receipts.move_line_ids.filtered(lambda line: line.quantity > 0)
        sources = []
        for line in lines:
            other_source = self.env['stock.move.line'].sudo().search_count([
                ('state', '=', 'done'), ('product_id', '=', line.product_id.id),
                ('location_dest_id', '=', quarantine.id), ('lot_id', '=', line.lot_id.id),
                ('result_package_id', '=', line.result_package_id.id), ('owner_id', '=', line.owner_id.id),
                ('quantity', '>', 0), ('move_id', 'not in', receipts.ids),
            ], limit=1)
            converted = [unit._compute_quantity(1, uom, round=False)
                         for uom in (line.product_uom_id, line.move_id.product_uom, line.product_id.uom_id)]
            uoms = (line.product_uom_id, line.move_id.product_uom, line.product_id.uom_id)
            sources.append({
                'id': line.id, 'move_id': line.move_id.id, 'lot_id': line.lot_id.id,
                'company_id': line.company_id.id, 'product_id': line.product_id.id,
                'package_id': line.result_package_id.id, 'owner_id': line.owner_id.id,
                'quantity': line.product_uom_id._compute_quantity(line.quantity, unit, round=False),
                'exclusive': not other_source,
                'partial_representable': all(isclose(value / uom.rounding, round(value / uom.rounding), abs_tol=1e-9)
                                             for value, uom in zip(converted, uoms)),
            })
        checks = self._quality_matrix_checks()
        facts = []
        for check in checks:
            structure = True
            try:
                check._validate_measurement_sample_structure()
            except ValidationError:
                structure = False
            lot = check.automatic_sample_lot_id or check.lot_id
            if check.automatic_sample_lot_id and check.lot_id and check.automatic_sample_lot_id != check.lot_id:
                structure = False
            facts.append({
                'id': check.id, 'point_id': check.point_id.id, 'workorder_id': check.workorder_id.id,
                'lot_id': lot.id, 'state': check.quality_state,
                'automatic_count': check.automatic_sample_count,
                'structure_valid': structure and not check.quality_recheck_of_id,
                'equipment_complete': check.equipment_selection_complete,
                'samples': [{'id': sample.id, 'result': sample.sample_result, 'complete': sample.is_complete}
                            for sample in check.sample_ids],
            })
        return receipts, checks, sources, facts

    def _quality_apply_disposition(self):
        self.ensure_one()
        if self.state != 'done' or not self.quality_quarantine_point_ids:
            return
        # Same serialization point used by the existing QC and board paths.
        self.write({'write_date': fields.Datetime.now()})
        self.invalidate_recordset()
        if self.quality_disposition_ids:
            return
        receipts, checks, sources, facts = self._quality_disposition_facts()
        plan = plan_production_release(sources, facts, self.quality_quarantine_point_ids.ids, self.product_tracking)
        note = '\n'.join(REASONS[code] for code in plan['reasons'])
        context = dict(self.env.context, **{PRODUCTION_DISPOSITION_CONTEXT: PRODUCTION_DISPOSITION_OPERATION})
        self.with_context(context).write({'quality_partial_release_note': note or False})
        if not plan['ready']:
            return
        releases = receipts.quality_release_move_ids
        if (len(releases) != len(receipts) or any(move.state != 'draft' for move in releases)
                or any(len(receipt.quality_release_move_ids) != 1 for receipt in receipts)):
            raise UserError(_('Фактичний випуск вже оброблено або немає єдиного захищеного руху.'))
        allocated = sum(plan['allocations'].values())
        if allocated == plan['produced']:
            # Preserve the working full-PASS executor, including physical lines.
            self._quality_complete_release(releases)
        elif allocated:
            complete_partial_production_release(releases, plan['allocations'])
        decision = self.env['quality.production.disposition'].sudo().with_context(context).create({
            'production_id': self.id, 'check_ids': [Command.set(checks.ids)],
            'source_move_ids': [Command.set(receipts.ids)], 'release_move_ids': [Command.set(releases.ids)],
            'produced_qty': plan['produced'], 'good_qty': plan['good'], 'bad_qty': plan['bad'],
            'unidentified_qty': plan['unidentified'], 'reason': note or False,
            'decided_by_id': self.env.uid, 'snapshot': {'sources': sources, 'checks': facts, 'plan': plan},
        })
        self.message_post(body=_(
            'Розподіл ВТК: виготовлено %(total)s, випущено %(released)s, у карантині %(held)s. Початкові QC збережено.',
            total=plan['produced'], released=decision.released_qty, held=decision.remaining_qty),
            subtype_xmlid='mail.mt_note')

    def action_open_quality_disposition(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        return {'type': 'ir.actions.act_window', 'name': _('Розподіл ВТК'),
                'res_model': 'quality.production.disposition', 'view_mode': 'tree,form',
                'domain': [('production_id', '=', self.id)]}
