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
                                  'vataga_initiator_id', 'vataga_recipient_id', 'vataga_request_status', 'vataga_rfq_created'])
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

    def test_diagnostic_uses_native_field_dependency_api(self):
        from odoo import fields
        self.assertEqual(fields.Char().get_depends(None), ((), ()))
        tree = ast.parse((ROOT / 'scripts/production_field_diagnostic.py').read_text(encoding='utf-8'))
        self.assertFalse(any(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                             and node.value.id == 'field' and node.attr == 'depends' for node in ast.walk(tree)))
