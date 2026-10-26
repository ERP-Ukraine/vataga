"""Enterprise-specific contract tests; NOT replaced by the native probe."""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .test_quality_unit_stock_release import QualityUnitStockReleaseCase


@tagged('post_install', '-at_install')
class TestQualityStockPostingIntegration(QualityUnitStockReleaseCase):
    def test_enterprise_entry_stays_closed_even_with_roles_permission_plan_and_context_flags(self):
        check, source, last, units, target, permission = self._authorized()
        production = check.production_id
        self._wizard(production, permission.unit_id).action_prepare()
        before = self._stock_snapshot(production)
        scope = self.env['quality.stock.cost.scope'].new({'production_id': production.id})
        with self.assertRaises(UserError):
            self.env['quality.stock.posting'].with_context(
                integration_validated=True, quality_stock_kernel=True, posting_available=True,
            )._post_one(scope, 'qunit:%s' % permission.unit_id.id, 'permission:%s' % permission.id, 'enterprise-gate')
        self.assertEqual(before, self._stock_snapshot(production))
        self.assertFalse(self.env['quality.stock.posting'].search([('production_id', '=', production.id)]))

    def test_direct_fake_qunit_posting_and_scope_creation_are_denied(self):
        check, source, last, units, target, permission = self._authorized()
        for model in ('quality.stock.posting', 'quality.stock.cost.scope'):
            with self.assertRaises(UserError):
                self.env[model].with_context(quality_stock_kernel=True).create({'production_id': check.production_id.id})

    def test_real_qunit_permission_plan_relations_and_uniqueness_are_required(self):
        model = self.env['quality.stock.posting']
        for name, comodel in [('unit_id', 'quality.production.unit'), ('permission_id', 'quality.unit.release.line'),
                              ('plan_id', 'quality.unit.stock.release.plan')]:
            self.assertTrue(model._fields[name].required)
            self.assertEqual(model._fields[name].comodel_name, comodel)
        definitions = {definition for _name, definition, _message in model._sql_constraints}
        self.assertIn('unique(unit_id)', definitions)
        self.assertIn('unique(permission_id)', definitions)

    def test_unattributed_source_operation_is_not_silently_zero_cost(self):
        check, source, last, units, target, permission = self._authorized()
        before = self._stock_snapshot(check.production_id)
        # This existing 13.3 fixture has no named cost interval on the source
        # operation. It must not fabricate one from a QC PASS or from zero.
        with self.assertRaises(ValidationError):
            self.env['quality.stock.cost.scope']._freeze_from_executions(check.production_id, {})
        self.assertEqual(before, self._stock_snapshot(check.production_id))
