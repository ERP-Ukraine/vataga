from odoo import _, models
from odoo.exceptions import UserError


class ReportStockPickingTransferLabel(models.AbstractModel):
    _name = 'report.product_vataga.report_stock_picking_transfer_label'
    _description = 'Vataga Transfer Label Report'

    def _get_report_values(self, docids, data=None):
        pickings = self.env['stock.picking'].browse(docids)
        labels = []

        for picking in pickings:
            moves = picking.move_ids.filtered(
                lambda move: move.product_id and move.state != 'cancel'
            ).sorted(lambda move: (move.sequence, move.id))
            total_moves = len(moves)
            if not total_moves:
                continue

            source_name = picking.location_id.complete_name or picking.location_id.display_name or ''
            destination_name = (
                picking.location_dest_id.complete_name
                or picking.location_dest_id.display_name
                or ''
            )

            for index, move in enumerate(moves, start=1):
                labels.append({
                    'picking': picking,
                    'move': move,
                    'picking_name': picking.name or '',
                    'source_name': source_name,
                    'destination_name': destination_name,
                    'sequence_text': _('%(current)02d / %(total)02d', current=index, total=total_moves),
                })

        if not labels:
            raise UserError(_('There are no product lines to print labels for.'))

        return {
            'docs': pickings,
            'labels': labels,
        }
