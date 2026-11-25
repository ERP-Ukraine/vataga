"""Isolated licensed test DB only: full supplied Studio views, never shared UAT."""
from pathlib import Path
from lxml import etree
from odoo.tests import tagged
from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestProductionStudio(PurchaseApprovalCase):
    def studio_view(self, kind, priority, xml_id, parent_xml_id):
        payload = (Path(__file__).parent / 'fixtures' / ('production_studio_%s.xml' % kind)).read_text(encoding='utf8')
        existing = self.env.ref(xml_id, raise_if_not_found=False)
        if existing:
            parser = etree.XMLParser(remove_blank_text=True)
            self.assertEqual(etree.tostring(etree.fromstring(existing.arch_db.encode(), parser), method='c14n'),
                             etree.tostring(etree.fromstring(payload.encode(), parser), method='c14n'))
            self.assertEqual(existing.priority, priority)
            self.assertEqual(existing.inherit_id, self.env.ref(parent_xml_id))
            return existing
        # Temporary view in TransactionCase rollback, retaining the FULL actual
        # XML and priority. No production IDs, no foreign XML-ID creation.
        return self.env['ir.ui.view'].create({
            'name': 'TEST-VATAGA-APPROVALS production Studio ' + kind,
            'model': 'approval.request', 'inherit_id': self.env.ref(parent_xml_id).id,
            'priority': priority, 'arch_db': payload,
        })

    def test_actual_production_form_with_native_inheritance_and_record_preservation(self):
        employee = self.env['hr.employee'].create({'name': 'TEST-VATAGA-APPROVALS Studio', 'company_id': self.company.id})
        request = self.request(x_studio_iniciation=employee.id, x_studio_who_recived=employee.id)
        before = request.read(['name','category_id','request_owner_id','request_status',
                               'x_studio_iniciation','x_studio_who_recived'])[0]
        view = self.studio_view('form', 160,
            'studio_customization.odoo_studio_approval_b0d5c6ab-c35d-4780-a1fe-7fbf979462c6',
            'approvals.approval_request_view_form')
        view_before = view.read(['arch_db','priority','inherit_id'])[0]
        arch = etree.fromstring(self.env['approval.request'].get_view(
            self.env.ref('approvals.approval_request_view_form').id, 'form')['arch'].encode())
        for name in ('x_studio_iniciation','x_studio_who_recived'):
            nodes = arch.xpath(".//field[@name='%s']" % name)
            self.assertEqual(len(nodes), 1)
            self.assertIn('category_id not in [9, 12]', nodes[0].get('invisible'))
            self.assertIn("approval_type == 'purchase'", nodes[0].get('invisible'))
            self.assertIn('vataga_lines_locked', nodes[0].get('readonly'))
        request.invalidate_recordset()
        self.assertEqual(request.read(list(before.keys()))[0], before)
        self.assertEqual(view.read(['arch_db','priority','inherit_id'])[0], view_before)

    def test_actual_production_tree_keeps_native_structure_and_optional_people(self):
        self.studio_view('tree', 99,
            'studio_customization.odoo_studio_approval_35bca016-a3a0-46ef-b7e7-9dc97af8e0ec',
            'approvals.approval_request_view_tree')
        arch = etree.fromstring(self.env['approval.request'].get_view(
            self.env.ref('approvals.approval_request_view_tree').id, 'tree')['arch'].encode())
        self.assertEqual(arch.tag, 'tree')
        for name, optional in [('x_studio_who_recived','show'), ('x_studio_iniciation','hide')]:
            nodes = arch.xpath("./field[@name='%s']" % name)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get('optional'), optional)
        for name in ('name','request_owner_id','category_id','request_status'):
            self.assertTrue(arch.xpath("./field[@name='%s']" % name))
