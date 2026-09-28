import json

from odoo import http
from odoo.http import content_disposition, request


class StockMinimumExport(http.Controller):
    @http.route('/stock_minimum_control/export_xlsx', type='http', auth='user', methods=['POST'])
    def export_xlsx(self, options, allowed_company_ids=None):
        report = request.env['stock.minimum.control.report']
        if allowed_company_ids:
            report = report.with_context(allowed_company_ids=json.loads(allowed_company_ids))
        content = report._export_xlsx(json.loads(options))
        return request.make_response(content, headers=[
            ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ('Content-Disposition', content_disposition('Контроль залишків.xlsx')),
        ])
