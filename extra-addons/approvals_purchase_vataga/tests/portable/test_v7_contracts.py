"""Executable scope/presentation contracts; not Enterprise workflow evidence."""
from contextvars import copy_context
import importlib
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lxml import etree

from support import ROOT, PACKAGE

scope_module = importlib.import_module(PACKAGE + '.rfq_scope')


class TestNewRFQScope(unittest.TestCase):
    def test_scope_is_cursor_bound_and_always_cleans_up(self):
        cursor, other = object(), object()
        self.assertIsNone(scope_module.current_scope(cursor))
        with self.assertRaisesRegex(RuntimeError, 'failure'):
            with scope_module.new_rfq_scope(cursor) as scope:
                self.assertIs(scope_module.current_scope(cursor), scope)
                self.assertIsNone(scope_module.current_scope(other))
                raise RuntimeError('failure')
        self.assertIsNone(scope_module.current_scope(cursor))

    def test_independent_nested_requests_never_share_key_or_orders(self):
        cursor = object()
        with scope_module.new_rfq_scope(cursor) as outer:
            outer.order_ids.add(123)
            with scope_module.new_rfq_scope(cursor) as inner:
                self.assertNotEqual(outer.key, inner.key)
                self.assertFalse(inner.order_ids)
            self.assertIs(scope_module.current_scope(cursor), outer)

    def test_another_execution_context_cannot_see_scope(self):
        cursor = object()
        clean = copy_context()
        with scope_module.new_rfq_scope(cursor):
            self.assertIsNone(clean.run(scope_module.current_scope, cursor))

    def test_form_omits_number_and_information_but_preserves_button_guard(self):
        arch = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        self.assertFalse(arch.xpath("//field[@name='vataga_display_number' or @name='vataga_rfq_block_reason']"))
        self.assertEqual(arch.xpath("//xpath[@expr=\"//button[@name='action_create_purchase_orders']\"]/attribute[@name='invisible']/text()"), ['not vataga_can_create_rfq'])
        topic = arch.xpath("//field[@name='vataga_purchase_subject']")[0]
        self.assertIsNone(topic.get('required'))


try:
    from test_odoo_contracts import models, request_module
    from odoo.exceptions import ValidationError
    company_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.res_company')
    order_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.purchase_order')
except ImportError:
    company_module = None


@unittest.skipIf(company_module is None, 'Odoo contract runtime unavailable')
class TestV7OdooContracts(unittest.TestCase):
    def test_purchase_search_signature_matches_real_17(self):
        import inspect
        self.assertEqual(list(inspect.signature(models.BaseModel._search).parameters),
                         list(inspect.signature(order_module.PurchaseOrder._search).parameters))

    def test_empty_automatic_subject_is_not_a_submission_error(self):
        line = SimpleNamespace(product_id=True, description='Valid', vataga_product_url=False,
                               quantity=1, product_uom_id=False)
        request = SimpleNamespace(approval_type='purchase', vataga_purchase_managed=True,
                                  automated_sequence=True, vataga_purchase_subject=False,
                                  product_line_ids=[line], display_name='TEST')
        class Requests(list):
            def filtered(self, fn):
                return [r for r in self if fn(r)]
        request_module.ApprovalRequest._vataga_validate_lines(Requests([request]))

    def test_missing_configuration_is_not_resolved_by_arbitrary_name_search(self):
        company = SimpleNamespace(ensure_one=lambda: None, vataga_default_supplier_id=False)
        with patch.object(company_module, '_', lambda value: value), self.assertRaises(ValidationError):
            company_module.ResCompany._vataga_default_supplier(company)


if __name__ == '__main__':
    unittest.main()
