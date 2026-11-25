"""Exact user-provided production XML; no replacement Studio skeletons."""
from copy import deepcopy
import importlib
import os
from pathlib import Path
import unittest

from lxml import etree
from support import ROOT, PACKAGE

studio = importlib.import_module(PACKAGE + '.studio_fields')
FIXTURES = ROOT / 'tests/fixtures'


class TestProductionStudioXML(unittest.TestCase):
    def fixture(self, kind):
        return etree.parse(str(FIXTURES / ('production_studio_%s.xml' % kind))).getroot()

    def test_full_production_form_modifiers_for_all_types_and_categories(self):
        source = self.fixture('form')
        original = etree.tostring(source)
        self.assertEqual(source[0].get('expr'), "//field[@name='category_id']")
        self.assertEqual(source[0].get('position'), 'after')
        self.assertEqual([n.get('name') for n in source[0]], list(studio.EMPLOYEE_FIELDS))
        self.assertEqual([n.get('invisible') for n in source[0]], ['category_id not in [9, 12]'] * 2)
        # Run the real helper on the entire provided document, not a substitute.
        result = studio.employee_form_arch(source)
        for category in (False, 9, 12, 1, 777):
            for kind in ('purchase', False, 'contract', 'other'):
                for locked in (False, True):
                    context = dict(category_id=category, approval_type=kind, vataga_lines_locked=locked)
                    for name in studio.EMPLOYEE_FIELDS:
                        nodes = result.xpath(".//field[@name='%s']" % name)
                        self.assertEqual(len(nodes), 1)
                        self.assertEqual(eval(nodes[0].get('invisible'), {}, context),
                                         False if kind == 'purchase' else category not in (9, 12))
                        self.assertEqual(eval(nodes[0].get('readonly'), {}, context),
                                         locked if kind == 'purchase' else False)
                        self.assertFalse(eval(nodes[0].get('required'), {}, context))
        self.assertEqual(etree.tostring(source), original, 'Never modify supplied Studio XML')

    def test_complete_production_tree_keeps_its_columns_and_optional_flags(self):
        source = self.fixture('tree')
        self.assertEqual(len(source), 2)
        self.assertEqual(source[0].get('expr'), "//tree[1]/field[@name='name']")
        self.assertEqual(source[1].get('expr'), "//field[@name='request_owner_id']")
        self.assertEqual([(n.get('name'), n.get('optional')) for n in source[0]],
                         [('create_date','show'), ('date_start','show'), ('date_end','show'), ('partner_id','show')])
        self.assertEqual([(n.get('name'), n.get('optional')) for n in source[1]],
                         [('x_studio_who_recived','show'), ('x_studio_iniciation','hide')])
        # The addon must not install these fixtures or override approval.request tree.
        import ast
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text(encoding='utf8'))
        self.assertFalse(any('fixtures' in path for path in manifest['data']))
        views = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        self.assertFalse(views.xpath("//field[@name='inherit_id' and @ref='approvals.approval_request_view_tree']"))

    def actual_arch(self, name):
        directory = os.environ.get('VATAGA_STAGING_ARCH_DIR')
        if not directory:
            self.skipTest('Read-only real staging architecture export required; no synthetic fallback')
        return etree.parse(str(Path(directory) / name)).getroot()

    def native_apply(self, source, specs):
        odoo_source = os.environ.get('VATAGA_ODOO_SOURCE')
        if odoo_source:
            import sys
            sys.path.insert(0, odoo_source)
        try:
            from odoo.tools.template_inheritance import apply_inheritance_specs
        except ImportError as error:
            self.skipTest('Real Odoo17 inheritance engine required: ' + str(error))
        return apply_inheritance_specs(source, deepcopy(specs))

    def test_exact_form_on_full_observed_staging_architecture(self):
        source = self.actual_arch('staging-combined-form.xml')
        self.assertEqual(source.tag, 'form')
        self.assertTrue(source.xpath(".//field[@name='product_line_ids']/tree"))
        self.assertTrue(source.xpath(".//field[@name='approver_ids']/tree"))
        # Undo only the post-inheritance fallback on a LOCAL COPY. Actual Studio
        # runs before _get_view adds missing nodes. Preserve every ERP/native node.
        for name in studio.EMPLOYEE_FIELDS:
            nodes = source.xpath(".//field[@name='%s']" % name)
            self.assertEqual(len(nodes), 1)
            self.assertIn('no_create', nodes[0].get('options', ''))
            nodes[0].getparent().remove(nodes[0])
        original = etree.tostring(source)
        inherited = self.native_apply(deepcopy(source), self.fixture('form'))
        category = inherited.xpath(".//field[@name='category_id']")[0]
        self.assertEqual(category.getnext().get('name'), studio.EMPLOYEE_FIELDS[0])
        self.assertEqual(category.getnext().getnext().get('name'), studio.EMPLOYEE_FIELDS[1])
        result = studio.employee_form_arch(inherited)
        twice = studio.employee_form_arch(result)
        for name in studio.EMPLOYEE_FIELDS:
            self.assertEqual(len(twice.xpath(".//field[@name='%s']" % name)), 1)
        own = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        for record_id in ('view_approval_request_vataga_purchase', 'view_approval_request_vataga_late_period_ui'):
            for spec in own.xpath("//record[@id='%s']/field[@name='arch']/xpath" % record_id):
                self.assertTrue(result.xpath(spec.get('expr')), spec.get('expr'))
        self.assertEqual(etree.tostring(source), original)

    def test_exact_tree_on_full_observed_native_tree(self):
        source = self.actual_arch('staging-native-tree.xml')
        self.assertEqual(source.tag, 'tree')
        inherited = self.native_apply(deepcopy(source), self.fixture('tree'))
        expected = ['name','create_date','date_start','date_end','partner_id','request_owner_id',
                    'x_studio_who_recived','x_studio_iniciation','category_id','activity_ids','request_status']
        self.assertEqual([node.get('name') for node in inherited if node.tag == 'field'], expected)
        self.assertEqual(inherited.get('create'), source.get('create'))
        self.assertEqual(inherited.get('decoration-info'), source.get('decoration-info'))
        self.assertEqual(inherited.xpath("./field[@name='x_studio_iniciation']/@optional"), ['hide'])
        self.assertEqual(inherited.xpath("./field[@name='x_studio_who_recived']/@optional"), ['show'])
