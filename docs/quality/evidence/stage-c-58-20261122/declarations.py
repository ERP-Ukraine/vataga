"""Enterprise boundary declarations; no claim to validate EE matrix/Shop Floor."""
from markupsafe import Markup
from odoo import fields, models


class Stage(models.Model):
    _name = 'quality.alert.stage'
    _description = 'Declared EE stage boundary'
    name = fields.Char()
    sequence = fields.Integer()


class Team(models.Model):
    _name = 'quality.alert.team'
    _description = 'Declared EE team boundary'
    name = fields.Char()


class Point(models.Model):
    _name = 'quality.point'
    _description = 'Declared EE point boundary'
    name = fields.Char()


class Alert(models.Model):
    _name = 'quality.alert'
    _inherit = ['mail.thread']
    _description = 'Declared EE Alert base; real C extensions loaded'
    name = fields.Char()
    description = fields.Html()
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    check_id = fields.Many2one('quality.check')
    product_id = fields.Many2one('product.product')
    product_tmpl_id = fields.Many2one('product.template')
    lot_id = fields.Many2one('stock.lot')
    picking_id = fields.Many2one('stock.picking')
    team_id = fields.Many2one('quality.alert.team')
    stage_id = fields.Many2one('quality.alert.stage')


class Check(models.Model):
    _name = 'quality.check'
    _description = 'Declared EE matrix input; real QC validation NOT loaded'
    name = fields.Char(default='Contract QC')
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    production_id = fields.Many2one('mrp.production')
    workorder_id = fields.Many2one('mrp.workorder')
    product_id = fields.Many2one('product.product')
    lot_id = fields.Many2one('stock.lot')
    automatic_sample_lot_id = fields.Many2one('stock.lot')
    automatic_sample_count = fields.Integer(default=5)
    point_id = fields.Many2one('quality.point')
    team_id = fields.Many2one('quality.alert.team')
    picking_id = fields.Many2one('stock.picking')
    incoming_hold_id = fields.Many2one('quality.incoming.hold')
    quality_recheck_of_id = fields.Many2one('quality.check')
    quality_state = fields.Selection([('none', 'None'), ('pass', 'PASS'), ('fail', 'FAIL')], default='none')
    x_control_stage = fields.Char(default='in_process')
    equipment_selection_complete = fields.Boolean(default=True)
    sample_ids = fields.One2many('quality.check.sample', 'quality_check_id')

    def _validate_measurement_sample_structure(self):
        return True  # Declared boundary; not the real EE matrix validation.

    def _get_technical_board_failure_description(self):
        return Markup('<p>Declared visual FAIL evidence</p>')

    def update_measurement_visual_result(self, sample_id, value):
        self.sample_ids.filtered(lambda row: row.id == sample_id).write({
            'visual': value, 'sample_result': 'pass' if value == 'yes' else 'fail', 'is_complete': True})

    def do_fail(self):
        return self.write({'quality_state': 'fail'})

    def do_pass(self):
        return self.write({'quality_state': 'pass'})

    def write(self, vals):
        result = super().write(vals)
        if 'quality_state' in vals:
            self.production_id._quality_release_if_passed()
        return result

    def do_alert(self):
        return False


class Sample(models.Model):
    _name = 'quality.check.sample'
    _description = 'Declared matrix result input'
    quality_check_id = fields.Many2one('quality.check')
    sample_number = fields.Integer()
    sample_result = fields.Selection([('pass', 'PASS'), ('fail', 'FAIL')])
    is_complete = fields.Boolean()
    visual = fields.Char()
    initial_failures = fields.Json()

    def _measurement_snapshot(self):
        return {'visual': {'value': self.visual, 'display': self.visual}}


class Production(models.Model):
    _inherit = 'mrp.production'
    quality_quarantine_point_ids = fields.Many2many('quality.point')

    def _quality_quarantine_location(self):
        return self.picking_type_id.quality_quarantine_location_id

    def _quality_matrix_checks(self):
        return self.env['quality.check'].search([('production_id', 'in', self.ids)])

    def _quality_release_if_passed(self):
        return None

    def _quality_complete_release(self, moves):
        from .production_release_stock import complete_partial_production_release
        return complete_partial_production_release(moves, {
            line.id: line.quantity for line in moves.quality_held_move_id.move_line_ids})
