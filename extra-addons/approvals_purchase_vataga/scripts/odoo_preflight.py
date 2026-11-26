"""Read-only preflight. Run with `odoo shell ... < this_file` BEFORE install.

Does not install, update, approve, commit, or mutate business records. No
Enterprise source text is emitted; only signatures, paths, hashes and proof.
"""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import textwrap


def preflight(env):
    required = {
        'approval.request': ['approval_type', 'category_id', 'request_owner_id', 'request_status',
                             'product_line_ids', 'automated_sequence', 'name', 'reference',
                             'date_start', 'date_end', 'partner_id', 'create_uid', 'company_id'],
        'approval.product.line': ['approval_request_id', 'product_id', 'description',
                                  'quantity', 'product_uom_id', 'purchase_order_line_id'],
        'approval.category': ['approval_type', 'automated_sequence'],
    }
    report = {'models': {}, 'approval_mro': [], 'xmlids': {}, 'rules': []}
    for name, names in required.items():
        model = env[name]
        report['models'][name] = {
            key: ({'type': model._fields[key].type, 'required': model._fields[key].required,
                   'compute': str(model._fields[key].compute), 'store': model._fields[key].store}
                  if key in model._fields else 'MISSING') for key in names
        }
        report['models'][name]['custom_fields'] = [key for key in model._fields if key.startswith('x_')]
        report['models'][name]['table'] = model._table
        report['models'][name]['auto'] = model._auto
    for cls in type(env['approval.request']).__mro__:
        method = cls.__dict__.get('action_approve')
        if method:
            item = {'module': cls.__module__, 'class': cls.__name__, 'signature': str(inspect.signature(method))}
            item['vataga_bridge_installed'] = bool(getattr(method, '_vataga_validation_bridge', False))
            try:
                source = textwrap.dedent(inspect.getsource(method))
                ast.parse(source)
                item['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
                item['file'] = inspect.getsourcefile(method)
            except (OSError, TypeError, SyntaxError):
                item['source'] = 'UNAVAILABLE'
            report['approval_mro'].append(item)
    for xmlid in ('approvals.approval_request_view_form',
                  'approvals_purchase.approval_purchase_request_view_form_inherit',
                  'approvals.approval_product_line_view_tree',
                  'approvals.approval_product_kanban_mobile_view',
                  'approvals.approvals_menu_root', 'purchase.group_purchase_user',
                  'approvals.group_approval_user', 'approvals.group_approval_manager',
                  'product.product_template_form_view'):
        record = env.ref(xmlid, raise_if_not_found=False)
        report['xmlids'][xmlid] = {'exists': bool(record), 'model': record._name if record else None}
    rules = env['ir.rule'].search([('model_id.model', 'in', list(required))])
    report['rules'] = rules.read(['name', 'domain_force', 'perm_read', 'perm_write', 'perm_create', 'perm_unlink'])
    report['product_url_fields'] = {
        name: [key for key in env[name]._fields if any(token in key.lower() for token in ('url', 'website'))]
        for name in ('product.template', 'product.product')
    }
    report['seller_helper_signature'] = (str(inspect.signature(env['approval.product.line']._get_seller_id))
                                          if hasattr(env['approval.product.line'], '_get_seller_id') else None)
    report['rfq_mro'] = []
    for model_name, names in (
        ('approval.request', ('action_create_purchase_orders',)),
        ('approval.product.line', ('_get_purchase_orders_domain', '_get_seller_id')),
    ):
        for cls in type(env[model_name]).__mro__:
            for name in names:
                method = cls.__dict__.get(name)
                if method:
                    item = {'model': model_name, 'method': name, 'module': cls.__module__,
                            'signature': str(inspect.signature(method))}
                    try:
                        source = textwrap.dedent(inspect.getsource(method))
                        item['file'] = inspect.getsourcefile(method)
                        item['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
                    except (OSError, TypeError):
                        item['source'] = 'UNAVAILABLE'
                    report['rfq_mro'].append(item)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if 'env' in globals():
    preflight(env)
