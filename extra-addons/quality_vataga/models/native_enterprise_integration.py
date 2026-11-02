"""Private capability for NEW staging fixtures on an always-rolled-back cursor.

No environment/context flag, persistent approval or RPC unlock is provided.
The caller's transaction, users, ACL and existing productions are untouched.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from odoo import api
from odoo.exceptions import AccessError, ValidationError

from .native_cost_provider import reviewed_enterprise_cost


_INTEGRATION = ContextVar('quality_enterprise_rollback_integration', default=None)
STAGING_DATABASE = 'odoo17-vataga-staging'


class _FixtureScope:
    def __init__(self, model, diagnostics):
        self.env = model.env
        self.diagnostics = diagnostics
        self.production_ids = set()
        self.created = {}
        self.env.cr.execute('SELECT COALESCE(MAX(id), 0) FROM mrp_production')
        self.previous_production_id = self.env.cr.fetchone()[0]

    def register(self, production):
        production.ensure_one()
        if (production.env.cr is not self.env.cr or production.env.uid != self.env.uid
                or production.id <= self.previous_production_id
                or production.create_uid.id != self.env.uid
                or production.company_id not in self.env.companies
                or production._native_partial_history()):
            raise AccessError('Only new, unposted productions from this rollback fixture may be admitted')
        self.production_ids.add(production.id)
        self.remember(production)

    def remember(self, records):
        # Only call with records created by the fixture. Ordinary access is
        # checked before collecting IDs; SQL below verifies absence only.
        records.check_access_rights('read')
        records.check_access_rule('read')
        self.created.setdefault(records._table, set()).update(records.ids)


@contextmanager
def enterprise_fixture_transaction(model, diagnostics):
    if model.env.cr.dbname != STAGING_DATABASE or not model.env.user.has_group('base.group_system'):
        raise AccessError('Enterprise integration is restricted to the staging administrator')
    context = {'allowed_company_ids': model.env.companies.ids,
               'lang': model.env.lang, 'tz': model.env.user.tz}
    with model.env.registry.cursor() as cursor:
        env = api.Environment(cursor, model.env.uid, context)
        fixture = env[model._name].with_company(model.env.company.id)
        scope = _FixtureScope(fixture, diagnostics)
        token = _INTEGRATION.set(scope)
        try:
            yield fixture, scope
        finally:
            try:
                cursor.rollback()
                diagnostics['rollback'] = 'ROLLED_BACK'
                verified = {}
                for table, ids in scope.created.items():
                    if not ids:
                        continue
                    # Tables come from registered ORM models, never RPC input.
                    quoted = '"' + table.replace('"', '""') + '"'
                    cursor.execute('SELECT id FROM ' + quoted + ' WHERE id IN %s', [tuple(ids)])
                    if cursor.fetchall():
                        raise ValidationError('Rollback left fixture rows in ' + table)
                    verified[table] = len(ids)
                diagnostics['rollback_verified_absent'] = verified
                # The absence checks must not leave even a read transaction
                # or a callback to be committed when the cursor closes.
                cursor.rollback()
                diagnostics['rollback'] = 'COMPLETE'
            finally:
                _INTEGRATION.reset(token)


def integration_cost_methods(workorder, native, core):
    scope = _INTEGRATION.get()
    if (not scope or len(workorder) != 1 or workorder.env.cr is not scope.env.cr
            or workorder.env.uid != scope.env.uid
            or workorder.production_id.id not in scope.production_ids):
        return ()
    provider = reviewed_enterprise_cost(workorder, native, core)
    if not provider or not workorder._native_analytic_duration_chain_reviewed():
        raise ValidationError('Enterprise cost source, file, MRO or duration chain differs from the reviewed staging source')
    return (provider,)
