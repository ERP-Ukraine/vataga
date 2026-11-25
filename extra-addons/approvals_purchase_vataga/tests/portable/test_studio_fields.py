import importlib
from types import SimpleNamespace
import unittest
from lxml import etree
from support import PACKAGE

studio = importlib.import_module(PACKAGE + '.studio_fields')


class TestStudioFields(unittest.TestCase):
    def test_missing_and_incompatible_metadata_fail_closed(self):
        good = dict(type='many2one', comodel_name='hr.employee', store=True, required=False, readonly=False)
        fields = {n: SimpleNamespace(**good) for n in studio.EMPLOYEE_FIELDS}
        studio.validate_employee_fields(fields)
        for change in ({'comodel_name': 'res.partner'}, {'type': 'char'}, {'required': True}, {'readonly': True}, {'store': False}):
            with self.subTest(change=change), self.assertRaises(TypeError):
                studio.validate_employee_fields(dict(fields, x_studio_iniciation=SimpleNamespace(**dict(good, **change))))
        with self.assertRaises(TypeError):
            studio.validate_employee_fields({})

    def test_staging_missing_nodes_are_added_once_without_mutating_input(self):
        source = etree.fromstring('<form><sheet><group><field name="request_owner_id"/></group></sheet></form>')
        result = studio.employee_form_arch(source)
        twice = studio.employee_form_arch(result)
        self.assertEqual(len(source.xpath('.//field')), 1)
        for name in studio.EMPLOYEE_FIELDS:
            self.assertEqual(len(twice.xpath(".//field[@name='%s']" % name)), 1)
            field = result.xpath(".//field[@name='%s']" % name)[0]
            self.assertTrue(eval(field.get('invisible'), {}, dict(approval_type='other')))
            self.assertFalse(eval(field.get('required'), {}, dict(approval_type='purchase')))

    def test_production_studio_nodes_keep_nonpurchase_behavior_and_layout(self):
        source = etree.fromstring('''<form><sheet><group name="studio160">
          <field name="x_studio_iniciation" readonly="other_lock" required="other_required" domain="[('name', '!=', 'Hidden')]"/>
          <field name="x_studio_who_recived"/>
        </group></sheet></form>''')
        result = studio.employee_form_arch(source)
        first = result.xpath(".//field[@name='x_studio_iniciation']")[0]
        self.assertEqual(first.getparent().get('name'), 'studio160')
        for kind, locked, expected in [('purchase', True, True), ('purchase', False, False), ('other', True, False)]:
            self.assertEqual(eval(first.get('readonly'), {}, dict(approval_type=kind, vataga_lines_locked=locked, other_lock=False)), expected)
        self.assertTrue(eval(first.get('required'), {}, dict(approval_type='other', other_required=True)))
        context = dict(approval_type='other', company_id=1)
        self.assertEqual(eval(first.get('domain'), {}, context), [('name', '!=', 'Hidden')])
        context['approval_type'] = 'purchase'
        self.assertEqual(eval(first.get('domain'), {}, context), [('name', '!=', 'Hidden'), ('active', '=', True), ('company_id', 'in', [False, 1])])
        self.assertEqual(len(result.xpath(".//field[@name='x_studio_who_recived']")), 1)

    def test_one_existing_studio_node_without_owner_and_shared_company(self):
        source = etree.fromstring('<form><group><field name="x_studio_iniciation"/></group></form>')
        result = studio.employee_form_arch(source)
        second = result.xpath(".//field[@name='x_studio_who_recived']")[0]
        self.assertEqual(eval(second.get('domain'), {}, dict(approval_type='purchase', company_id=False)), [('active', '=', True)])

    def test_unrelated_simplified_form_is_unchanged(self):
        source = etree.fromstring('<form><field name="name"/></form>')
        self.assertEqual(etree.tostring(studio.employee_form_arch(source)), etree.tostring(source))
