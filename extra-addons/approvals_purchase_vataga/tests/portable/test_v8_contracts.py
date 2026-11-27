"""Real Odoo17 APIs and extracted addon policies; no Enterprise ORM claim."""
import ast
import importlib
import inspect
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lxml import etree
from support import ROOT
from test_odoo_contracts import line_module, request_module, RequestSet, models
from odoo.exceptions import ValidationError
from odoo.tools import SQL
from odoo.tools.query import Query

order_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.purchase_order')
product_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.product_product')


class TestV8Contracts(unittest.TestCase):
    def test_subject_is_checked_only_for_new_purchase_and_never_accepts_number(self):
        for automatic in (False, True):
            for value in (False, '', ' \t\n'):
                record = SimpleNamespace(approval_type='purchase', request_status='new',
                    automated_sequence=automatic, vataga_purchase_subject=value,
                    name='APPR00020' if automatic else value)
                with patch.object(request_module, '_', lambda value: value), self.assertRaises(ValidationError):
                    request_module.ApprovalRequest._vataga_validate_subject(RequestSet([record]))
                record.vataga_purchase_subject = record.name = 'Valid topic'
                request_module.ApprovalRequest._vataga_validate_subject(RequestSet([record]))
        for kind, status in [('purchase', 'approved'), ('purchase', 'pending'), (False, 'new')]:
            record = SimpleNamespace(approval_type=kind, request_status=status)
            request_module.ApprovalRequest._vataga_validate_subject(RequestSet([record]))

    def test_native_nonpurchase_name_requirement_preserved(self):
        for kind, name, fails in [('purchase', False, False), (False, False, True), (False, ' ', False)]:
            records = [SimpleNamespace(approval_type=kind, name=name)]
            with patch.object(request_module, '_', lambda value: value):
                if fails:
                    with self.assertRaises(ValidationError):
                        request_module.ApprovalRequest._vataga_native_name_required(records)
                else:
                    request_module.ApprovalRequest._vataga_native_name_required(records)

    def test_custom_sql_hooks_match_native_17_signatures(self):
        for name in ('_field_to_sql', '_order_field_to_sql'):
            ours = inspect.signature(getattr(line_module.ApprovalProductLine, name))
            native = inspect.signature(getattr(models.BaseModel, name))
            self.assertEqual(list(ours.parameters), list(native.parameters))
        self.assertEqual(list(inspect.signature(product_module.ProductProduct.web_save).parameters),
                         ['self', 'vals', 'specification', 'next_id'])

    def test_po_sql_group_expression_uses_native_visibility_query_and_no_sudo(self):
        cursor = SimpleNamespace()
        visible = Query(cursor, 'purchase_order_line')
        visible.add_where(SQL('%s = %s', SQL.identifier('purchase_order_line', 'company_id'), 37))
        model = SimpleNamespace(
            _vataga_readable_purchase_lines=lambda: visible,
            flush_model=lambda names: None,
            env={'purchase.order.line': SimpleNamespace(flush_model=lambda names: None)})
        value = line_module.ApprovalProductLine._field_to_sql(model, 'approval_product_line', 'vataga_purchase_order_id')
        self.assertIsInstance(value, SQL)
        self.assertIn('vp.id IN (SELECT', value.code)
        self.assertIn('"approval_product_line"."purchase_order_line_id"', value.code)
        self.assertEqual(value.params, [37])
        model._vataga_readable_purchase_lines = lambda: None
        self.assertEqual(line_module.ApprovalProductLine._field_to_sql(
            model, 'approval_product_line', 'vataga_purchase_order_id').code, 'NULL::integer')

    def test_hidden_order_is_empty_for_positive_negative_and_empty_search(self):
        from odoo.osv import expression
        model = SimpleNamespace(_vataga_readable_purchase_lines=lambda domain=(): None)
        method = line_module.ApprovalProductLine._search_vataga_purchase_order
        for op, val in [('=', 20), ('ilike', 'P14920'), ('!=', False)]:
            self.assertEqual(method(model, op, val), expression.FALSE_DOMAIN)
        for op, val in [('=', False), ('!=', 20), ('not ilike', 'P14920')]:
            result = method(model, op, val)
            self.assertTrue(result == [] or result == expression.TRUE_DOMAIN)

    def test_sources_are_relational_copy_false_and_user_visible_compute_is_not_stored(self):
        order = order_module.PurchaseOrder
        self.assertFalse(order.vataga_origin_approval_ids.copy)
        self.assertFalse(order.vataga_rfq_origin_key.copy)
        self.assertEqual(order.vataga_approval_request_ids.comodel_name, 'approval.request')
        field = line_module.ApprovalProductLine.vataga_purchase_order_id
        self.assertFalse(field.store)
        self.assertFalse(field.compute_sudo)

    def test_labels_and_registry_search_group_contract(self):
        for name in ('views/approval_request_views.xml', 'wizard/approval_product_resolve_views.xml'):
            text = (ROOT / name).read_text(encoding='utf-8')
            self.assertNotIn('Заповнити', text)
            self.assertIn('Створити товар', text)
        wizard = etree.parse(str(ROOT / 'wizard/approval_product_resolve_views.xml'))
        self.assertEqual(wizard.xpath('//button[@name="action_apply"]/@string'), ['Створити'])
        self.assertEqual(wizard.xpath('//button[@name="action_apply_open_product"]/@string'), ['Створити та редагувати'])
        registry = etree.parse(str(ROOT / 'views/purchase_request_registry_views.xml'))
        self.assertTrue(registry.xpath('//search/field[@name="vataga_purchase_order_id"]'))
        self.assertEqual(ast.literal_eval(registry.xpath('//filter[@name="group_purchase_order"]/@context')[0]),
                         {'group_by': 'vataga_purchase_order_id'})


if __name__ == '__main__':
    unittest.main()
