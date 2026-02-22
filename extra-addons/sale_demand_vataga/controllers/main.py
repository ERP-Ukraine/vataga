from collections import deque
import io
import json

from werkzeug.datastructures import FileStorage

from odoo import http, _
from odoo.http import content_disposition, request
from odoo.tools import ustr, osutil
from odoo.tools.misc import xlsxwriter


class TableExporterDemand(http.Controller):
    @http.route('/web/pivot/demand/export_xlsx', type='http', auth='user')
    def export_xlsx(self, data, **kw):
        jdata = json.load(data) if isinstance(data, FileStorage) else json.loads(data)
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet(jdata['title'])

        header_bold = workbook.add_format(
            {'bold': True, 'pattern': 1, 'bg_color': '#AAAAAA'}
        )
        header_plain = workbook.add_format({'pattern': 1, 'bg_color': '#AAAAAA'})
        bold = workbook.add_format({'bold': True})

        measure_count = jdata['measure_count']
        origin_count = jdata['origin_count']

        # Step 1: writing col group headers
        col_group_headers = jdata['col_group_headers']

        # x,y: current coordinates
        # carry: queue containing cell information when a cell has a >= 2 height
        #      and the drawing code needs to add empty cells below
        x, y, carry = 1, 0, deque()
        for i, header_row in enumerate(col_group_headers):
            worksheet.write(i, 0, '', header_plain)
            for header in header_row:
                while carry and carry[0]['x'] == x:
                    cell = carry.popleft()
                    for j in range(measure_count * (2 * origin_count - 1)):
                        worksheet.write(y, x + j, '', header_plain)
                    if cell['height'] > 1:
                        carry.append({'x': x, 'height': cell['height'] - 1})
                    x = x + measure_count * (2 * origin_count - 1)
                for j in range(header['width']):
                    worksheet.write(
                        y, x + j, header['title'] if j == 0 else '', header_plain
                    )
                if header['height'] > 1:
                    carry.append({'x': x, 'height': header['height'] - 1})
                x = x + header['width']
            while carry and carry[0]['x'] == x:
                cell = carry.popleft()
                for j in range(measure_count * (2 * origin_count - 1)):
                    worksheet.write(y, x + j, '', header_plain)
                if cell['height'] > 1:
                    carry.append({'x': x, 'height': cell['height'] - 1})
                x = x + measure_count * (2 * origin_count - 1)
            x, y = 1, y + 1

        # Step 2: writing measure headers
        measure_headers = jdata['measure_headers']
        
        # ERP
        closed_columns = []
        # ERP end

        if measure_headers:
            worksheet.write(y, 0, '', header_plain)
            for measure in measure_headers:
                style = header_bold if measure['is_bold'] else header_plain
                # ERP
                if measure['title'] == _('Closed'):
                    closed_columns.append(x)
                # ERP end
                worksheet.write(y, x, measure['title'], style)
                for i in range(1, 2 * origin_count - 1):
                    worksheet.write(y, x + i, '', header_plain)
                x = x + (2 * origin_count - 1)
            x, y = 1, y + 1
            # set minimum width of cells to 16 which is around 88px
            worksheet.set_column(0, len(measure_headers), 16)

        # Step 3: writing origin headers
        origin_headers = jdata['origin_headers']

        if origin_headers:
            worksheet.write(y, 0, '', header_plain)
            for origin in origin_headers:
                style = header_bold if origin['is_bold'] else header_plain
                worksheet.write(y, x, origin['title'], style)
                x = x + 1
            y = y + 1

        # Step 4: writing data
        x = 0
        for row in jdata['rows']:
            worksheet.write(
                y, x, row['indent'] * '     ' + ustr(row['title']), header_plain
            )
            for cell in row['values']:
                x = x + 1
                # ERP
                if x in closed_columns and cell['value']:
                    closed = round(cell['value'] * 100, 2)
                    closed_percent = f'{closed}%'
                    custom_style_dict = {}
                    if cell.get('is_bold', False):
                        custom_style_dict.update({'bold': True})
                    if closed < 70:
                        custom_style_dict.update({'bg_color': '#d9bfc7'})
                    elif closed < 100:
                        custom_style_dict.update({'bg_color': '#e4daa8'})
                    elif closed == 100:
                        custom_style_dict.update({'bg_color': '#71a064'})
                    else:
                        custom_style_dict.update({'bg_color': '#779bb5'})
                    custom_style = workbook.add_format(custom_style_dict)
                    worksheet.write(y, x, closed_percent, custom_style)
                # ERP end
                elif cell.get('is_bold', False):
                    worksheet.write(y, x, cell['value'], bold)
                else:
                    worksheet.write(y, x, cell['value'])
            x, y = 0, y + 1

        workbook.close()
        xlsx_data = output.getvalue()
        filename = osutil.clean_filename(
            _(
                'Pivot %(title)s (%(model_name)s)',
                title=jdata['title'],
                model_name=jdata['model'],
            )
        )
        response = request.make_response(
            xlsx_data,
            headers=[
                (
                    'Content-Type',
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                ),
                ('Content-Disposition', content_disposition(filename + '.xlsx')),
            ],
        )

        return response
