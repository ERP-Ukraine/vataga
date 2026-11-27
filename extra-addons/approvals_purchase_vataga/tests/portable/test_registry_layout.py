import ast
import unittest
from lxml import etree
from support import ROOT


class TestRegistryLayout(unittest.TestCase):
    def test_default_order_and_optional_columns(self):
        tree = etree.parse(str(ROOT / 'views/purchase_request_registry_views.xml')).xpath('//tree')[0]
        visible = [f.get('name') for f in tree.findall('field') if f.get('optional') != 'hide']
        self.assertEqual(visible, ['vataga_request_number', 'vataga_request_date', 'vataga_category_id',
                                  'product_id', 'description', 'vataga_product_url', 'quantity', 'product_uom_id',
                                  'vataga_employee_initiator_id', 'vataga_employee_recipient_id', 'vataga_request_status', 'vataga_purchase_order_id'])
        hidden = {f.get('name') for f in tree.findall('field') if f.get('optional') == 'hide'}
        self.assertEqual(hidden, {'vataga_request_subject', 'vataga_owner_id', 'vataga_date_start', 'vataga_date_end',
                                 'vataga_partner_id', 'vataga_filled_after_approval', 'vataga_product_created_from_request', 'company_id'})
        self.assertEqual(tree.xpath("field[@name='company_id']")[0].get('groups'), 'base.group_multi_company')
        self.assertFalse(tree.findall('button'))

    def test_status_decorations_match_real_selection(self):
        tree = etree.parse(str(ROOT / 'views/purchase_request_registry_views.xml')).xpath('//tree')[0]
        node = tree.xpath("field[@name='vataga_request_status']")[0]
        colors = {'new': 'muted', 'pending': 'warning', 'approved': 'success', 'refused': 'danger', 'cancel': 'muted'}
        for status, color in colors.items():
            applied = [key[11:] for key, expr in node.attrib.items() if key.startswith('decoration-')
                       and eval(expr, {'__builtins__': {}}, {'vataga_request_status': status})]
            self.assertEqual(applied, [color])

    def test_diagnostic_has_no_write_calls_or_imports(self):
        script = (ROOT / 'scripts/production_field_diagnostic.py').read_text(encoding='utf-8')
        tree = ast.parse(script)
        for node in ast.walk(tree):
            self.assertNotIsInstance(node, (ast.Import, ast.ImportFrom))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {'create', 'write', 'unlink', 'execute', 'sudo', 'commit', 'log'})

    def test_diagnostic_compiles_in_native_safe_eval(self):
        from odoo.tools.safe_eval import test_expr, _SAFE_OPCODES
        test_expr((ROOT / 'scripts/production_field_diagnostic.py').read_text(encoding='utf-8'), _SAFE_OPCODES, mode='exec')

    def test_production_counts_diagnostic_is_native_safe_eval_and_read_only(self):
        from odoo.tools.safe_eval import test_expr, _SAFE_OPCODES, safe_eval
        from odoo.exceptions import UserError
        script = (ROOT / 'scripts/production_safe_eval_diagnostic.py').read_text(encoding='utf8')
        self.assertIn("('id', 'not in', linked_ids)", script)
        self.assertNotIn("('product_line_ids.purchase_order_line_id', '=', False)", script,
                         'Mixed sibling links must not also count as no retained link')
        test_expr(script, _SAFE_OPCODES, mode='exec')
        tree = ast.parse(script)
        forbidden = {'create','write','unlink','sudo','execute','commit','rollback','log',
                     'open','eval','exec','getattr','setattr','__import__'}
        for node in ast.walk(tree):
            self.assertNotIsInstance(node, (ast.Import, ast.ImportFrom))
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                self.assertNotIn(name, forbidden)
        # Denied sections remain BLOCKED, without exception text/PII or fake zero.
        from types import SimpleNamespace
        class Denied:
            cr = SimpleNamespace(dbname='diagnostic_test')
            companies = SimpleNamespace(ids=[1])
            def __getitem__(self, model):
                raise Exception('SECRET EMPLOYEE NAME')
        with self.assertRaises(UserError) as raised:
            safe_eval(script, {'env':Denied(), 'UserError':UserError}, mode='exec', nocopy=True)
        message = str(raised.exception)
        self.assertIn('BLOCKED', message)
        self.assertNotIn('SECRET EMPLOYEE NAME', message)
        self.assertIn('Database: diagnostic_test', message)

    def test_diagnostic_uses_native_field_dependency_api(self):
        from odoo import fields
        self.assertEqual(fields.Char().get_depends(None), ((), ()))
        tree = ast.parse((ROOT / 'scripts/production_field_diagnostic.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                             and node.value.id == 'field' and node.attr == 'depends' for node in ast.walk(tree)))
