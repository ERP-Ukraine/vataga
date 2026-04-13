from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _get_vataga_label_moves(self):
        self.ensure_one()
        return self.move_ids_without_package.filtered(
            lambda move: move.product_id and move.state != 'cancel'
        ).sorted(lambda move: (move.sequence, move.id))

    def action_print_vataga_transfer_labels(self):
        printable_pickings = self.filtered(lambda picking: picking._get_vataga_label_moves())
        if not printable_pickings:
            raise UserError(_('There are no product lines to print labels for.'))

        return self.env.ref(
            'product_vataga.action_report_stock_picking_transfer_labels_v2'
        ).report_action(printable_pickings, config=False)

    def action_open_label_layout(self):
        return self.action_print_vataga_transfer_labels()

    def action_open_label_type(self):
        return self.action_print_vataga_transfer_labels()
