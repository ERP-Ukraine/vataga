from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError


class ReportStockPickingTransferLabel(models.AbstractModel):
    _name = 'report.product_vataga.report_stock_picking_transfer_label_v2'
    _description = 'Vataga Transfer Label Report'

    @staticmethod
    def _to_html_entities(text):
        return Markup(''.join(
            char if ord(char) < 128 else f'&#{ord(char)};'
            for char in (text or '')
        ))

    def _get_report_values(self, docids, data=None):
        pickings = self.env['stock.picking'].browse(docids)
        labels = []

        for picking in pickings:
            moves = picking.move_ids_without_package.filtered(
                lambda move: move.product_id and move.state != 'cancel'
            ).sorted(lambda move: (move.sequence, move.id))
            total = len(moves)
            if not total:
                continue

            source_name, destination_name = picking._get_vataga_transfer_label_locations()

            for index, move in enumerate(moves, start=1):
                labels.append({
                    'picking_name': self._to_html_entities(picking.name or ''),
                    'source_line_text': self._to_html_entities(
                        f'Джерело: {source_name}'
                    ),
                    'destination_line_text': self._to_html_entities(
                        f'Призначення: {destination_name}'
                    ),
                    'sequence_text': f'{index:02d} / {total:02d}',
                })

        if not labels:
            raise UserError(_('There are no product lines to print labels for.'))

        return {
            'docs': pickings,
            'labels': labels,
        }
