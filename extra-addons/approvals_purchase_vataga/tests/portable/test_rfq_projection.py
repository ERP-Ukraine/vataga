import ast
from types import SimpleNamespace
import unittest
from lxml import etree
from support import ROOT


class TestRFQProjection(unittest.TestCase):
    def test_actual_compute_truth_table_and_audit_preservation(self):
        tree = ast.parse((ROOT / 'models/approval_request.py').read_text(encoding='utf8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_compute_vataga_rfq_exists')
        method.decorator_list = []
        module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
        namespace = {}
        exec(compile(module, 'actual-rfq-projection', 'exec'), namespace)
        for audit, links, expected in [(False,[],False),(True,[],True),(False,[71],True),(True,[71],True)]:
            record = SimpleNamespace(vataga_rfq_created=audit,
                product_line_ids=SimpleNamespace(purchase_order_line_id=links))
            namespace['_compute_vataga_rfq_exists']([record])
            self.assertIs(record.vataga_rfq_exists, expected)
            self.assertIs(record.vataga_rfq_created, audit)

    def test_actual_po_column_preserves_fact_filter_and_group(self):
        document = etree.parse(str(ROOT / 'views/purchase_request_registry_views.xml'))
        self.assertEqual(len(document.xpath('//tree/field[@name="vataga_purchase_order_id"]')), 1)
        self.assertFalse(document.xpath('//tree/field[@name="vataga_rfq_created"]'))
        self.assertEqual(ast.literal_eval(document.xpath('//filter[@name="rfq_created"]/@domain')[0]),
                         [('vataga_rfq_exists','=',True)])
        self.assertEqual(ast.literal_eval(document.xpath('//filter[@name="group_rfq"]/@context')[0]),
                         {'group_by':'vataga_rfq_exists'})
