from html import unescape

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged('post_install', '-at_install')
class TestBomLineAutolog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, lang='en_US'))
        cls.finished, cls.component, cls.replacement = cls.env['product.product'].create([
            {'name': 'Autolog Finished Product'},
            {'name': 'Autolog Component', 'default_code': 'AUTOLOG-OLD'},
            {'name': 'Autolog Replacement', 'default_code': 'AUTOLOG-NEW'},
        ])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished.product_tmpl_id.id,
        })
        cls.line = cls.env['mrp.bom.line'].create({
            'bom_id': cls.bom.id,
            'product_id': cls.component.id,
            'product_qty': 15.0,
            'product_uom_id': cls.component.uom_id.id,
        })
        cls.dozen = cls.env.ref('uom.product_uom_dozen')
        workcenter = cls.env['mrp.workcenter'].create({'name': 'Autolog Workcenter'})
        cls.operation = cls.env['mrp.routing.workcenter'].create({
            'name': 'Autolog Assembly',
            'bom_id': cls.bom.id,
            'workcenter_id': workcenter.id,
        })
        cls.subtype = cls.env.ref('mrp_vataga.mt_bom_autolog')

    def _write_and_get_body(self, vals):
        domain = [
            ('model', '=', 'mrp.bom'),
            ('res_id', '=', self.bom.id),
            ('subtype_id', '=', self.subtype.id),
        ]
        previous = self.env['mail.message'].search(domain, order='id desc', limit=1)
        self.line.write(vals)
        messages = self.env['mail.message'].search(
            domain + [('id', '>', previous.id or 0)], order='id desc',
        )
        self.assertEqual(len(messages), 1)
        body = unescape(str(messages.body))
        self.assertIn('Змінено компонент: %s; ' % self.line.product_id.display_name, body)
        return body

    def test_quantity_only(self):
        body = self._write_and_get_body({'product_qty': 45.0})
        self.assertIn(self.component.display_name, body)
        self.assertIn('Quantity: 15.0 -> 45.0', body)

    def test_uom(self):
        old_uom = self.line.product_uom_id.display_name
        body = self._write_and_get_body({'product_uom_id': self.dozen.id})
        label = self.line._fields['product_uom_id'].string
        self.assertIn('%s: %s -> %s' % (label, old_uom, self.dozen.display_name), body)

    def test_other_tracked_field(self):
        old_operation = self.line._format_tracked_value('operation_id')
        body = self._write_and_get_body({'operation_id': self.operation.id})
        label = self.line._fields['operation_id'].string
        self.assertIn('%s: %s -> %s' % (label, old_operation, self.operation.display_name), body)

    def test_component_replacement(self):
        old_product = self.component.display_name
        body = self._write_and_get_body({'product_id': self.replacement.id})
        self.assertIn('Component: %s -> %s' % (old_product, self.replacement.display_name), body)

    def test_multiple_fields(self):
        old_product = self.component.display_name
        old_uom = self.line.product_uom_id.display_name
        body = self._write_and_get_body({
            'product_qty': 45.0,
            'product_id': self.replacement.id,
            'product_uom_id': self.dozen.id,
        })
        label = self.line._fields['product_uom_id'].string
        self.assertIn(
            'Quantity: 15.0 -> 45.0; Component: %s -> %s; %s: %s -> %s' % (
                old_product, self.replacement.display_name, label, old_uom, self.dozen.display_name,
            ),
            body,
        )
