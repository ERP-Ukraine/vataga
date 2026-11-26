# Manual Odoo 17 server action: Execute Python Code. READ ONLY.
# A scheduled action has no popup UI: UserError will be recorded as a failure.
# Current access/company scope only; BLOCKED never means zero.
report = ['VATAGA READ-ONLY / Odoo 17 / production preflight']
report.append('Database: ' + str(env.cr.dbname))
report.append('Scope: current permissions; companies=' + str(env.companies.ids))
for name in ['approvals_purchase_vataga', 'approvals', 'approvals_purchase', 'purchase', 'hr']:
    try:
        module = env['ir.module.module'].search([('name', '=', name)], limit=1)
        report.append('Module %s: %s; installed=%s; available=%s' % (
            name, module.state if module else 'NOT FOUND',
            module.latest_version if module else '-', module.installed_version if module else '-'))
    except Exception:
        report.append('Module %s: BLOCKED (access/metadata)' % name)
for name in ['x_studio_iniciation', 'x_studio_who_recived']:
    try:
        items = env['ir.model.fields'].search([('model', '=', 'approval.request'), ('name', '=', name)])
        report.append('Field %s: metadata_count=%s' % (name, len(items)))
        for item in items:
            report.append(str({'name': item.name, 'type': item.ttype, 'relation': item.relation,
                'store': item.store, 'readonly': item.readonly, 'required': item.required,
                'state': item.state, 'modules': item.modules,
                'xml_id': item.get_external_id().get(item.id)}))
    except Exception:
        report.append('Field %s: BLOCKED (access/metadata)' % name)
try:
    refs = env['ir.model.data'].search([('module', '=', 'studio_customization'), ('model', '=', 'ir.ui.view')])
    views = env['ir.ui.view'].with_context(active_test=False).search([
        ('id', 'in', refs.mapped('res_id')), ('model', '=', 'approval.request')], order='priority,id')
    report.append('Studio approval.request views: ' + str(len(views)))
    for view in views:
        report.append(str({'id': view.id, 'xml_id': view.get_external_id().get(view.id),
            'type': view.type, 'active': view.active, 'priority': view.priority,
            'parent_xml_id': view.inherit_id.get_external_id().get(view.inherit_id.id) if view.inherit_id else False}))
except Exception:
    report.append('Studio views: BLOCKED (access/metadata)')
try:
    fields_meta = env['ir.model.fields'].search([
        ('model', 'in', ['approval.request', 'approval.product.line', 'product.template', 'purchase.order', 'res.company'])])
    possible = fields_meta.filtered(lambda f: f.name.startswith('vataga_'))
    report.append('Existing vataga_* fields (review ownership against module status): ' + str(len(possible)))
    for item in possible:
        report.append(str({'model': item.model, 'name': item.name, 'type': item.ttype,
            'relation': item.relation, 'state': item.state, 'modules': item.modules}))
except Exception:
    report.append('vataga_* namespace: BLOCKED (access/metadata)')
purchase = [('approval_type', '=', 'purchase')]
counts = [
    ('Purchase requests', 'approval.request', purchase),
    ('Purchase item lines', 'approval.product.line', [('approval_request_id.approval_type', '=', 'purchase')]),
    ('Requests with retained RFQ link', 'approval.request', purchase + [('product_line_ids.purchase_order_line_id', '!=', False)]),
]
try:
    linked_ids = env['approval.request'].with_context(active_test=False).search(
        purchase + [('product_line_ids.purchase_order_line_id', '!=', False)]).ids
    counts.append(('Approved without retained RFQ link', 'approval.request',
        purchase + [('request_status', '=', 'approved'), ('id', 'not in', linked_ids)]))
except Exception:
    report.append('Approved without retained RFQ link: BLOCKED (access/model contract)')
try:
    request_model = env['approval.request']
    old = purchase + [('automated_sequence', '=', False)]
    if 'vataga_purchase_managed' in request_model._fields:
        old = old + [('vataga_purchase_managed', '=', False)]
        report.append('Old criterion: vataga_purchase_managed=False; not an inferred creation date.')
    else:
        report.append('Old criterion: all existing purchase requests; managed flag absent.')
    counts.append(('Old requests without automatic numbering', 'approval.request', old))
except Exception:
    report.append('Old requests criterion: BLOCKED (model/metadata)')
for title, model_name, domain in counts:
    try:
        report.append(title + ': ' + str(env[model_name].with_context(active_test=False).search_count(domain)))
    except Exception:
        report.append(title + ': BLOCKED (access/model contract)')
try:
    states = env['approval.request'].fields_get(['request_status'])['request_status']['selection']
    for code, label in states:
        total = env['approval.request'].with_context(active_test=False).search_count(purchase + [('request_status', '=', code)])
        report.append('Status %s: %s' % (code, total))
except Exception:
    report.append('Status distribution: BLOCKED (access/model contract)')
report.append('No employee names, document contents, SQL, sudo, imports or writes. Totals may be restricted by record rules.')
report.append('Without a retained link, never-created and previously-deleted RFQ cannot be distinguished.')
report.append('Shell/isolated runtime still required: code/MRO/AST proof, hashes, exact XML inheritance, schema/recompute/Install/rollback and concurrency. Other roles require their own authorized sessions.')
raise UserError('\n'.join(report))
