from odoo import _, api, fields, models
from odoo.exceptions import UserError


class QualityCheckTransfers(models.Model):
    _inherit = 'quality.check'

    unit_transfer_ids = fields.One2many(related='operation_run_id.unit_transfer_ids', readonly=True, compute_sudo=False)
    quality_transfer_controlled = fields.Boolean(related='operation_run_id.production_id.quality_transfer_controlled', readonly=True, compute_sudo=False)
    x_transferred_qty = fields.Integer(string='Передано', compute='_compute_transfer_quantities', help='Активні передачі, включно з уже прийнятими виробами. Повернені не враховуються.')
    x_received_qty = fields.Integer(string='Прийнято наступною операцією', compute='_compute_transfer_quantities', help='Прийнята частина переданих виробів. Не додавайте до кількості переданих.')
    x_awaiting_transfer_qty = fields.Integer(string='Очікує передачі', compute='_compute_transfer_quantities')

    @api.depends('operation_run_id.unit_transfer_ids.state', 'x_admitted_qty')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_transfer_quantities(self):
        for check in self:
            transfers = self.env['quality.unit.transfer'].search([
                ('source_run_id', '=', check.operation_run_id.id), ('state', 'in', ('sent', 'received')),
            ]) if check.operation_run_id else self.env['quality.unit.transfer']
            check.x_transferred_qty = len(transfers.controlled_unit_id)
            check.x_received_qty = len(transfers.filtered(lambda record: record.state == 'received').controlled_unit_id)
            check.x_awaiting_transfer_qty = max(check.x_admitted_qty - check.x_transferred_qty, 0)

    def action_open_unit_transfer_wizard(self):
        self.ensure_one()
        self._check_unit_admission_access()
        if not self.operation_run_id.workorder_id:
            raise UserError(_('Спочатку зареєструйте запуск з робочим завданням.'))
        return self.env['quality.unit.transfer.wizard']._open_action(
            self.operation_run_id.workorder_id, 'send', check=self,
        )

    def get_measurement_matrix_data(self):
        data = super().get_measurement_matrix_data()
        data.update(transferred_quantity=self.x_transferred_qty, received_quantity=self.x_received_qty, awaiting_transfer_quantity=self.x_awaiting_transfer_qty)
        transfers = self.env['quality.unit.transfer'].search([('source_run_id', '=', self.operation_run_id.id)]) if self.operation_run_id else self.env['quality.unit.transfer']
        latest = {}
        for record in transfers:
            latest.setdefault(record.controlled_unit_id.id, record)
        labels = dict(self.env['quality.unit.transfer']._fields['state'].selection)
        for row in data['samples']:
            transfer = latest.get(row.get('controlled_unit_id'))
            row['unit_transfer_label'] = labels[transfer.state] if transfer else ''
            row['unit_transfer_destination'] = transfer.destination_workorder_id.display_name if transfer else ''
        return data

    def write(self, vals):
        if {'unit_transfer_ids', 'x_transferred_qty', 'x_received_qty', 'x_awaiting_transfer_qty', 'quality_transfer_controlled'}.intersection(vals):
            raise UserError(_('Кількість та історію передач встановлює система.'))
        return super().write(vals)


class QualityRunTransfers(models.Model):
    _inherit = 'quality.production.run'

    unit_transfer_ids = fields.One2many('quality.unit.transfer', 'source_run_id', readonly=True)


class MrpWorkorderTransferViews(models.Model):
    _inherit = 'mrp.workorder'

    quality_transfer_controlled = fields.Boolean(related='production_id.quality_transfer_controlled', readonly=True, compute_sudo=False)
    quality_transfer_out_ids = fields.One2many('quality.unit.transfer', 'source_workorder_id', string='Вихідні передачі', readonly=True)
    quality_transfer_in_ids = fields.One2many('quality.unit.transfer', 'destination_workorder_id', string='Вхідні передачі', readonly=True)
    quality_transfer_has_run = fields.Boolean(compute='_compute_quality_transfer_counts')
    quality_admitted_qty = fields.Integer(string='Допущено', compute='_compute_quality_transfer_counts')
    quality_transferred_qty = fields.Integer(string='Передано', compute='_compute_quality_transfer_counts', help='Включає вже прийняті вироби; повернені не враховуються.')
    quality_received_qty = fields.Integer(string='Прийнято наступною операцією', compute='_compute_quality_transfer_counts', help='Прийнята частина переданих виробів.')
    quality_awaiting_transfer_qty = fields.Integer(string='Очікує передачі', compute='_compute_quality_transfer_counts')
    quality_held_qty = fields.Integer(string='Утримано', compute='_compute_quality_transfer_counts')
    quality_incoming_received_qty = fields.Integer(string='Прийнято цією операцією', compute='_compute_quality_transfer_counts')

    @api.depends('quality_transfer_out_ids.state', 'quality_transfer_in_ids.state', 'production_id.quality_transfer_revision')
    @api.depends_context('uid', 'allowed_company_ids')
    def _compute_quality_transfer_counts(self):
        for workorder in self:
            runs = self.env['quality.production.run'].search([('workorder_id', '=', workorder.id), ('state', '=', 'registered')])
            admissions = self.env['quality.unit.admission'].search([('operation_run_id', 'in', runs.ids), ('state', '=', 'confirmed')])
            admitted = admissions.filtered(lambda record: not record._get_effectiveness_error()).controlled_unit_id
            outgoing = self.env['quality.unit.transfer'].search([('source_workorder_id', '=', workorder.id), ('state', 'in', ('sent', 'received'))])
            incoming = self.env['quality.unit.transfer'].search([('destination_workorder_id', '=', workorder.id), ('state', '=', 'received')])
            workorder.quality_transfer_has_run = bool(runs)
            workorder.quality_admitted_qty = len(admitted)
            workorder.quality_transferred_qty = len(outgoing.controlled_unit_id)
            workorder.quality_received_qty = len(outgoing.filtered(lambda record: record.state == 'received').controlled_unit_id)
            workorder.quality_awaiting_transfer_qty = len(admitted - outgoing.controlled_unit_id)
            workorder.quality_held_qty = len(runs.unit_ids - admitted)
            workorder.quality_incoming_received_qty = len(incoming.controlled_unit_id)

    def action_open_quality_unit_transfer_wizard(self):
        self.ensure_one()
        checks = self.env['quality.check'].search([('operation_run_id.workorder_id', '=', self.id)])
        runs = checks.operation_run_id
        if len(runs) != 1:
            raise UserError(_('Для передачі з робочого завдання потрібен один однозначний запуск. За наявності кількох запусків відкрийте конкретну перевірку якості.'))
        return runs.origin_check_id.action_open_unit_transfer_wizard()

    def action_open_quality_unit_receipt_wizard(self):
        self.ensure_one()
        return self.env['quality.unit.transfer.wizard']._open_action(self, 'receive')

    def action_open_quality_unit_return_wizard(self):
        self.ensure_one()
        return self.env['quality.unit.transfer.wizard']._open_action(self, 'return')
