"""Native group/migration tests, independent of Quality business fixtures."""

import importlib.util
from pathlib import Path

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


def _source_module(name, relative_path):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = _source_module('quality_release_role_policy', 'models/quality_release_access.py')
migration = _source_module('quality_release_role_migration', 'migrations/17.0.2.38/post-migration.py')
LEGACY_GROUPS = ('group_quality_unit_release', 'group_quality_unit_stock_release')


@tagged('post_install', '-at_install')
class TestQualityReleaseAccessRoles(TransactionCase):
    def _user(self, login, groups):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref('base.group_user').id] + [self.env.ref(group).id for group in groups])],
        })

    def _without(self, user, group):
        # Native group membership removal, without SQL or a context bypass.
        self.env.ref(group).write({'users': [Command.unlink(user.id)]})
        self.assertFalse(user.has_group(group))

    def _legacy_groups(self, implied=False):
        groups = self.env['res.groups']
        for name in LEGACY_GROUPS:
            self.assertFalse(self.env.ref('quality_vataga.' + name, raise_if_not_found=False))
            group = self.env['res.groups'].create({
                'name': 'Legacy fixture ' + name,
                'implied_ids': [Command.set([self.env.ref(item).id for item in (
                    'mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_user',
                )])] if implied else [],
            })
            self.env['ir.model.data'].create({
                'module': 'quality_vataga', 'name': name, 'model': 'res.groups',
                'res_id': group.id, 'noupdate': True,
            })
            groups |= group
        return groups

    def test_both_native_roles_allow_preparation_policy(self):
        user = self._user('qunit_roles_both', list(policy.STOCK_RELEASE_GROUPS))
        self.assertTrue(policy.has_stock_release_roles(user))
        self.assertTrue(policy.check_stock_release_roles(user))

    def test_stock_without_mrp_is_denied(self):
        user = self._user('qunit_roles_stock', list(policy.STOCK_RELEASE_GROUPS))
        self._without(user, 'mrp.group_mrp_user')
        self.assertTrue(user.has_group('stock.group_stock_user'))
        self.assertFalse(policy.check_stock_release_roles(user, raise_exception=False))
        with self.assertRaises(AccessError):
            policy.check_stock_release_roles(user)

    def test_mrp_without_stock_is_denied_even_if_mrp_normally_implies_it(self):
        user = self._user('qunit_roles_mrp', list(policy.STOCK_RELEASE_GROUPS))
        self._without(user, 'stock.group_stock_user')
        self.assertTrue(user.has_group('mrp.group_mrp_user'))
        self.assertFalse(policy.has_stock_release_roles(user))
        with self.assertRaises(AccessError):
            policy.check_stock_release_roles(user)

    def test_neither_role_is_denied(self):
        user = self._user('qunit_roles_neither', [])
        self.assertFalse(policy.has_stock_release_roles(user))
        self.assertFalse(policy.check_stock_release_roles(user, raise_exception=False))

    def test_board_role_does_not_replace_mrp(self):
        user = self._user('qunit_roles_board_no_mrp', ['quality_vataga.group_quality_board_approval', *policy.STOCK_RELEASE_GROUPS])
        self._without(user, 'mrp.group_mrp_user')
        self.assertTrue(user.has_group('quality_vataga.group_quality_board_approval'))
        self.assertFalse(policy.has_stock_release_roles(user))

    def test_board_role_does_not_replace_stock(self):
        user = self._user('qunit_roles_board_no_stock', ['quality_vataga.group_quality_board_approval', *policy.STOCK_RELEASE_GROUPS])
        self._without(user, 'stock.group_stock_user')
        self.assertTrue(user.has_group('quality_vataga.group_quality_board_approval'))
        self.assertFalse(policy.has_stock_release_roles(user))

    def test_quality_manager_already_inherits_board_role(self):
        user = self._user('qunit_roles_quality_manager', ['quality.group_quality_manager'])
        self.assertTrue(user.has_group('quality_vataga.group_quality_board_approval'))
        ordinary = self._user('qunit_roles_quality_user', ['quality.group_quality_user'])
        self.assertFalse(ordinary.has_group('quality_vataga.group_quality_board_approval'))

    def test_legacy_role_without_native_memberships_is_not_authorization(self):
        groups = self._legacy_groups()
        user = self._user('qunit_roles_legacy_only', [])
        user.groups_id = [Command.link(group.id) for group in groups]
        self.assertFalse(policy.has_stock_release_roles(user))
        self.assertFalse(user.has_group('quality_vataga.group_quality_board_approval'))

    def test_upgrade_retires_groups_and_leftover_acls_without_granting_roles(self):
        groups = self._legacy_groups(implied=True)
        user = self._user('qunit_roles_legacy_upgrade', [])
        user.groups_id = [Command.link(group.id) for group in groups]
        manager = self._user('qunit_roles_existing_manager', ['quality.group_quality_manager'])
        before = set(user.groups_id.ids) - set(groups.ids)
        manager_before = set(manager.groups_id.ids)
        acl = self.env['ir.model.access'].create({
            'name': 'Legacy custom ACL fixture', 'model_id': self.env.ref('base.model_res_partner').id,
            'group_id': groups[0].id, 'perm_read': True,
        })
        migration.migrate(self.cr, '17.0.2.37')
        self.env.invalidate_all()
        self.assertFalse(groups.exists())
        self.assertFalse(acl.exists())
        self.assertEqual(set(user.groups_id.ids), before)
        self.assertEqual(set(manager.groups_id.ids), manager_before)
        self.assertFalse(user.has_group('quality_vataga.group_quality_board_approval'))
        for name in LEGACY_GROUPS:
            record = self.env.ref('quality_vataga.' + name, raise_if_not_found=False)
            self.assertFalse(record and record.exists())

    def test_upgrade_is_idempotent_and_does_not_assign_replacement_roles(self):
        user = self._user('qunit_roles_upgrade_empty', [])
        before = set(user.groups_id.ids)
        migration.migrate(self.cr, '17.0.2.37')
        migration.migrate(self.cr, '17.0.2.37')
        self.assertEqual(set(user.groups_id.ids), before)
        self.assertFalse(user.has_group('quality_vataga.group_quality_board_approval'))
