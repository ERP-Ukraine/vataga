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
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL
from odoo.tools.query import Query

order_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.purchase_order')
product_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.product_product')


class TestV8Contracts(unittest.TestCase):
    def test_actual_direct_approve_adapter_checks_new_subject_before_native_workflow(self):
        class Records(RequestSet):
            def filtered(self, predicate):
                return Records(record for record in self if predicate(record))
            def _vataga_lock(self):
                pass
            def _vataga_validate_subject(self):
                return request_module.ApprovalRequest._vataga_validate_subject(self)
        records = Records([SimpleNamespace(approval_type='purchase', request_status='new',
            automated_sequence=True, name='APPR00020', vataga_purchase_subject=False)])
        with patch.object(request_module, '_', lambda value: value), self.assertRaises(ValidationError):
            request_module.ApprovalRequest.action_approve(records)

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
        query = Query(cursor, 'approval_product_line')
        value = line_module.ApprovalProductLine._field_to_sql(model, 'approval_product_line', 'vataga_purchase_order_id', query)
        self.assertIsInstance(value, SQL)
        self.assertEqual(value.code, '"approval_product_line__vataga_readable_purchase_line"."order_id"')
        self.assertIn('LEFT JOIN', query.from_clause.code)
        self.assertIn('"approval_product_line"."purchase_order_line_id"', query.from_clause.code)
        self.assertEqual(query.from_clause.params, [37])
        # Odoo17 native Many2one group ordering takes the first token; it must
        # remain a complete column, not the truncated "(SELECT" seen in UAT.
        self.assertEqual(value.code.split()[0], value.code)
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

    def test_real_odoo17_many2one_group_order_keeps_complete_joined_column(self):
        # Execute the actual native parser that truncated the former scalar
        # SELECT to '(SELECT' on staging. No database/Enterprise simulation.
        column = SQL.identifier('approval_product_line__vataga_readable_purchase_line', 'order_id')
        class Env(dict):
            cr = SimpleNamespace(mogrify=lambda sql: sql.code.encode())
        record = SimpleNamespace(
            _fields={'vataga_purchase_order_id': SimpleNamespace(type='many2one', comodel_name='purchase.order')},
            env=Env({'purchase.order': SimpleNamespace(_order='name desc')}),
            _order_to_sql=lambda order, query: SQL('%s ASC', column))
        order, extra, fields_used = models.BaseModel._read_group_orderby(record,
            'vataga_purchase_order_id ASC', {'vataga_purchase_order_id': column}, Query(None, 'approval_product_line'))
        self.assertEqual(order.code, column.code + ' ASC')
        self.assertEqual(extra.code, column.code)
        self.assertEqual(fields_used, [])

    def test_po_search_uses_number_and_includes_null_fk_for_empty_and_negation(self):
        queries = []
        def visible(domain=()):
            queries.append(domain)
            return Query(None, 'purchase_order_line')
        record = SimpleNamespace(_vataga_readable_purchase_lines=visible)
        method = line_module.ApprovalProductLine._search_vataga_purchase_order
        for op, val in [('=', False), ('!=', 15174), ('not ilike', 'P14925')]:
            domain = method(record, op, val)
            self.assertIn(('purchase_order_line_id', '=', False), domain)
        method(record, 'ilike', 'P14925')
        self.assertEqual(queries[-1], [('name', 'ilike', 'P14925')])

    def test_known_empty_native_query_has_safe_empty_and_negative_semantics(self):
        empty = Query(None, 'purchase_order_line')
        empty.set_result_ids(())
        record = SimpleNamespace(_vataga_readable_purchase_lines=lambda domain=(): empty)
        method = line_module.ApprovalProductLine._search_vataga_purchase_order
        self.assertEqual(method(record, '=', False), [])
        self.assertEqual(method(record, '!=', 15174), [])

    def test_field_level_purchase_denial_masks_before_querying_names(self):
        def denied(*args):
            raise AccessError('TEST name field denied')
        order = SimpleNamespace(check_access_rights=lambda *args, **kwargs: True,
                                check_field_access_rights=denied)
        lines = SimpleNamespace(check_access_rights=lambda *args, **kwargs: True,
                                check_field_access_rights=lambda *args: None)
        record = SimpleNamespace(env={'purchase.order': order, 'purchase.order.line': lines},
                                 check_field_access_rights=lambda *args: None)
        self.assertIsNone(line_module.ApprovalProductLine._vataga_readable_purchase_lines(record))

    def test_no_purchase_acl_compute_does_not_read_the_protected_native_fk(self):
        class Line:
            @property
            def purchase_order_line_id(self):
                raise AssertionError('Hidden source must not be read')
        class Lines(list):
            def _vataga_readable_purchase_lines(self):
                return None
        line = Line()
        line_module.ApprovalProductLine._compute_vataga_purchase_order(Lines([line]))
        self.assertIs(line.vataga_purchase_order_id, False)

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
