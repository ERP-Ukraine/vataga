"""Read-only Odoo shell preflight; run with the OLD addon code still deployed.

No imports from the new addon, no creation, SQL, sudo, write or commit.
Missing fields must be created via native Technical Fields BEFORE .5 code.
Production fields are NOT created/renamed/re-owned by this script or addon.
"""


def studio_preflight(env):
    request = env['approval.request']
    result = {}
    for name in ('x_studio_iniciation', 'x_studio_who_recived'):
        field = request._fields.get(name)
        if (not field or field.type != 'many2one' or field.comodel_name != 'hr.employee'
                or not field.store or field.required or field.readonly):
            raise RuntimeError('BLOCKED: incompatible or missing approval.request.%s; do not deploy .5' % name)
        metadata = env['ir.model.fields'].search([('model', '=', 'approval.request'), ('name', '=', name)])
        if len(metadata) != 1:
            raise RuntimeError('BLOCKED: expected exactly one metadata row for ' + name)
        result[name] = dict(type=field.type, relation=field.comodel_name, store=field.store,
                            required=field.required, readonly=field.readonly,
                            state=metadata.state, xml_id=metadata.get_external_id().get(metadata.id))
    if 'hr.employee.public' not in env:
        raise RuntimeError('BLOCKED: hr.employee.public is not installed')
    print('PASS Studio prerequisites (metadata only):', result)
    return result


if 'env' in globals():
    studio_preflight(env)
