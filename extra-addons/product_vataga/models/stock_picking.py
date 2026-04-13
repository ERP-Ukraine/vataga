import html

from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    @staticmethod
    def _to_ascii_markup(text):
        return Markup(
            ''.join(
                html.escape(char) if ord(char) < 128 else f'&#{ord(char)};'
                for char in (text or '')
            )
        )

    def _get_vataga_label_moves(self):
        self.ensure_one()
        return self.move_ids_without_package.filtered(
            lambda move: move.product_id and move.state != 'cancel'
        ).sorted(lambda move: (move.sequence, move.id))

    def action_print_vataga_transfer_labels(self):
        labels = []

        for picking in self:
            moves = picking._get_vataga_label_moves()
            total = len(moves)
            if not total:
                continue

            source_name = picking.location_id.complete_name or ''
            destination_name = picking.location_dest_id.complete_name or ''

            for index, move in enumerate(moves, start=1):
                labels.append({
                    'picking_name_markup': self._to_ascii_markup(picking.name or ''),
                    'source_line_markup': self._to_ascii_markup(
                        f'Джерело: {source_name}'
                    ),
                    'destination_line_markup': self._to_ascii_markup(
                        f'Призначення: {destination_name}'
                    ),
                    'sequence_text': f'{index:02d} / {total:02d}',
                })

        if not labels:
            raise UserError(_('There are no product lines to print labels for.'))

        return self.env.ref(
            'product_vataga.action_report_stock_picking_transfer_labels'
        ).report_action(self, data={'labels': labels}, config=False)

    def action_open_label_layout(self):
        return self.action_print_vataga_transfer_labels()

    def action_open_label_type(self):
        return self.action_print_vataga_transfer_labels()
