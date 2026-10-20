"""Read-only probe, to run via `odoo-bin shell -d TEST_DB < this_file`.

Print field definitions only. No business records are read or written.
Use an authorized administrator account; this probe never calls sudo().
"""
import json


field = env['quality.alert']._fields.get('x_sorting_parameter')
runtime = None
if field:
    runtime = {
        'model': 'quality.alert',
        'name': 'x_sorting_parameter',
        'type': field.type,
        'store': field.store,
        'readonly': field.readonly,
        'required': field.required,
        'related': field.related,
        'compute': str(field.compute) if field.compute else False,
        'inverse': str(field.inverse) if field.inverse else False,
        'size': getattr(field, 'size', None),
        'module': getattr(field, '_module', None),
    }
metadata = env['ir.model.fields'].search_read([
    ('model', '=', 'quality.alert'), ('name', '=', 'x_sorting_parameter'),
], ['model', 'name', 'ttype', 'state', 'modules', 'relation', 'readonly', 'required', 'store'])
print(json.dumps({'runtime': runtime, 'metadata': metadata}, ensure_ascii=False, indent=2))
