"""Run the existing real repair/Alert/native-Backorder suite with explicit policy."""
from odoo.exceptions import UserError
from odoo.tests import tagged

from . import test_quality_native_backorder as native_tests


@tagged('post_install', '-at_install')
class TestRequiredUnitRouteNative(native_tests.TestQualityNativeBackorder):
    def _production(self, quantity=5, uom=None):
        production = super()._production(quantity, uom)
        production.action_require_quality_unit_route()
        self.assertTrue(production.quality_unit_route_required)
        return production

    def _children(self, production):
        children = super()._children(production)
        self.assertTrue(all(child.quality_unit_route_required for child in children))
        return children

    def test_complete_route_without_effective_admission_stays_blocked(self):
        production, source, final, units = self._ready(False)
        admission = final.unit_admission_ids[:1]
        self.assertTrue(admission)
        admission.with_user(self.manager)._revoke('Disposable missing final admission')
        with self.assertRaises(UserError):
            self._release(production, units)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(self._children(production))
