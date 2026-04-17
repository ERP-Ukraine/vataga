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
            total = max(picking.box_count or 1, 1)

            title_text = picking._get_vataga_transfer_label_title()
            source_line_text, destination_line_text = (
                picking._get_vataga_transfer_label_lines()
            )

            for index in range(1, total + 1):
                labels.append({
                    'picking_name': self._to_html_entities(title_text),
                    'source_line_text': self._to_html_entities(source_line_text),
                    'destination_line_text': self._to_html_entities(destination_line_text),
                    'sequence_text': f'{index:02d} / {total:02d}',
                })

        if not labels:
            raise UserError(_('There are no product lines to print labels for.'))

        return {
            'docs': pickings,
            'labels': labels,
        }
