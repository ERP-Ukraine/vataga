"""Retire QUNIT-only roles without granting replacement memberships."""

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Module upgrade administration only; operational methods never sudo.
    env = api.Environment(cr, SUPERUSER_ID, {})
    groups = env['res.groups']
    for name in ('group_quality_unit_release', 'group_quality_unit_stock_release'):
        group = env.ref('quality_vataga.' + name, raise_if_not_found=False)
        if group:
            groups |= group.exists()
    if groups:
        # The module CSV has already reassigned its own ACLs. Remove any
        # remaining ACLs on retired groups, never make them global and never
        # transfer them to a stronger role. group_id has ondelete=restrict.
        env['ir.model.access'].search([('group_id', 'in', groups.ids)]).unlink()
        groups.unlink()
