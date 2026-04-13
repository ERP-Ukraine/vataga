from odoo import _, models
from odoo.exceptions import UserError


class ReportStockPickingTransferLabel(models.AbstractModel):
    _name = 'report.product_vataga.report_stock_picking_transfer_label'
    _description = 'Vataga Transfer Label Report'

    def _get_report_values(self, docids, data=None):
        pickings = self.env['stock.picking'].browse(docids)
        labels = (data or {}).get('labels', [])

        if not labels:
            raise UserError(_('There are no product lines to print labels for.'))

        return {
            'docs': pickings,
            'labels': labels,
        }
