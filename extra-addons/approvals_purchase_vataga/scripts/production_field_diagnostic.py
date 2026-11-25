# Odoo 17 server action / Execute Python Code. Run manually as administrator.
# READ ONLY: metadata only, no business record values, no sudo, SQL or logging.
# The final UserError displays the report and prevents committing any action.
# Copy the complete popup text; do not run against production automatically.
report = ['VATAGA READ-ONLY FIELD DIAGNOSTIC / Odoo17']
for model_name in ['approval.request', 'approval.product.line']:
    model_meta = env['ir.model'].search([('model', '=', model_name)], limit=1)
    if not model_meta:
        report.append('MODEL ABSENT: ' + model_name)
        continue
    model = env[model_name]
    specs = model.fields_get()
    metadata = env['ir.model.fields'].search([('model', '=', model_name)])
    report.append('\nMODEL: ' + model_name + ' / modules=' + str(model_meta.modules))
    for meta in metadata.sorted('name'):
        if meta.name not in model._fields:
            continue
        field = model._fields[meta.name]
        spec = specs.get(meta.name, {})
        dependency_info = field.get_depends(model)
        report.append(str({
            'name': meta.name, 'label': spec.get('string', meta.field_description),
            'model': model_name, 'type': meta.ttype,
            'relation': meta.relation, 'related': field.related,
            'store': field.store, 'readonly': field.readonly,
            'required': field.required, 'compute': str(field.compute),
            'depends': str(dependency_info[0]), 'depends_context': str(dependency_info[1]),
            'metadata_depends': meta.depends,
            'state': meta.state, 'modules': meta.modules,
            'xml_id': meta.get_external_id().get(meta.id),
            'groups': field.groups,
        }))
    views = env['ir.ui.view'].with_context(active_test=False).search([('model', '=', model_name)])
    report.append('\nVIEWS (including inactive / inherited / Studio):')
    for view in views.sorted('id'):
        arch = view.arch_db or ''
        used = [name for name in sorted(specs) if ('"' + name + '"') in arch or ("'" + name + "'") in arch]
        report.append(str({
            'id': view.id, 'name': view.name, 'active': view.active,
            'xml_id': view.get_external_id().get(view.id),
            'inherit_id': view.inherit_id.id,
            'inherit_xml_id': view.inherit_id.get_external_id().get(view.inherit_id.id) if view.inherit_id else False,
            'priority': view.priority, 'fields': used,
        }))
report.append('\nEMPLOYEE MODEL OPTIONS (metadata only; no employee data):')
for model_name in ['hr.employee', 'hr.employee.public', 'res.users']:
    meta = env['ir.model'].search([('model', '=', model_name)], limit=1)
    if meta:
        model = env[model_name]
        report.append(str({'model': model_name, 'modules': meta.modules,
                           'read_acl_current_user': model.check_access_rights('read', raise_exception=False),
                           'identity_fields': [name for name in ['name', 'user_id', 'employee_id', 'company_id', 'active'] if name in model._fields]}))
    else:
        report.append('MODEL ABSENT: ' + model_name)
report.append('\nstate=manual means Studio/custom metadata; base+modules/XML ID identifies module origin. Base is not proof of standard Enterprise origin. View field links are textual candidates, not parsed inheritance proof. Current administrator ACL is not ordinary-user access proof.')
raise UserError('\n'.join(report))
