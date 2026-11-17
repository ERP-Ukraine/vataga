from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestProductAnalogAutolog(TransactionCase):
    def test_analog_lifecycle_without_reciprocal_or_duplicate_entries(self):
        template, analog = self.env['product.template'].create([
            {'name': 'Main audit product'}, {'name': 'Analog audit product'},
        ])
        variant = template.product_variant_id
        subtype = self.env.ref('product_vataga.mt_product_autolog')

        def logs(record):
            return record.message_ids.filtered(lambda msg: msg.subtype_id == subtype).sorted('id')

        variant.write({'analog_line_ids': [Command.create({'product_id': analog.product_variant_id.id})]})
        line = template.analog_line_ids
        self.assertIn('Додано аналог', logs(variant).body)
        self.assertTrue(line.reciprocal_line_id)
        self.assertFalse(logs(template) | logs(analog))
        same_line = line.create({'product_tmpl_id': template.id, 'product_id': line.product_id.id})
        self.assertEqual(same_line, line)
        self.assertFalse(logs(template) | logs(analog))
        line.write({'sequence': 21})
        self.assertIn('Змінено аналог', logs(template).body)
        line.write({'sequence': 21})
        line.unlink()
        self.assertEqual(len(logs(template)), 2)
        self.assertIn('Видалено аналог', logs(template)[-1].body)
        self.assertFalse(logs(analog))
