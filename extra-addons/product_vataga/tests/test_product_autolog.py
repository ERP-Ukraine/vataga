from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged('post_install', '-at_install')
class TestProductAutolog(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.template = cls.env['product.template'].create({
            'name': 'Test autolog', 'default_code': 'AUTO-001', 'barcode': '100000000081',
        })
        cls.variant = cls.template.product_variant_id
        cls.subtype = cls.env.ref('product_vataga.mt_product_autolog')

    def logs(self, record):
        return record.message_ids.filtered(lambda msg: msg.subtype_id == self.subtype).sorted('id')

    def test_change_and_noop(self):
        self.assertFalse(self.logs(self.template))
        self.template.write({'name': 'Test autolog'})
        self.assertFalse(self.logs(self.template))
        self.template.write({'name': 'Updated <product>'})
        log = self.logs(self.template)
        self.assertEqual(len(log), 1)
        self.assertIn('Назва: було «Test autolog», стало «Updated &lt;product&gt;»', log.body)
        self.assertEqual(log.author_id, self.env.user.partner_id)
        self.assertTrue(log.date)

    def test_template_inverse_and_multiple_fields(self):
        category = self.env['product.category'].create({'name': 'Матеріали'})
        self.template.write({
            'default_code': 'AUTO-002', 'barcode': '100000000082', 'categ_id': category.id,
            'description_sale': 'Оновлений опис', 'list_price': 15.5,
        })
        log = self.logs(self.template)
        self.assertEqual(len(log), 1)
        for value in ['Артикул', 'Штрихкод', 'Категорія', 'Опис для продажу', 'Ціна продажу', 'Матеріали']:
            self.assertIn(value, log.body)
        self.assertEqual(self.variant.default_code, 'AUTO-002')
        self.assertEqual(self.variant.barcode, '100000000082')
        self.assertFalse(self.logs(self.variant))

    def test_archive_and_unarchive(self):
        self.template.action_archive()
        self.template.action_unarchive()
        self.assertFalse(self.logs(self.variant))
        self.assertEqual(len(self.logs(self.template)), 2)
        self.assertIn('Активний: було «Так», стало «Ні»', self.logs(self.template)[0].body)
        self.assertIn('Активний: було «Ні», стало «Так»', self.logs(self.template)[1].body)

    def test_variant_delegated_and_local_fields(self):
        self.variant.write({
            'name': 'Edited through variant', 'default_code': 'VAR-002',
            'barcode': '100000000083', 'lst_price': 43,
        })
        self.assertFalse(self.logs(self.template))
        log = self.logs(self.variant)
        self.assertEqual(len(log), 1)
        for value in ['Назва', 'Артикул', 'Штрихкод', 'Ціна продажу']:
            self.assertIn(value, log.body)
        self.assertEqual(self.template.name, 'Edited through variant')
        self.assertEqual(self.template.list_price, 43)

    def test_variant_archive_does_not_log_template_synchronization(self):
        self.variant.action_archive()
        self.assertFalse(self.template.active)
        self.variant.action_unarchive()
        self.assertTrue(self.template.active)
        self.assertEqual(len(self.logs(self.variant)), 2)
        self.assertFalse(self.logs(self.template))

    def test_packaging_inline_and_direct_edit(self):
        self.template.write({'packaging_ids': [Command.create({'name': 'Коробка', 'qty': 6})]})
        packaging = self.variant.packaging_ids
        self.assertIn('Додано упаковку', self.logs(self.template).body)
        packaging.write({'qty': 10})
        self.assertIn('Кількість: було «6.0», стало «10.0»', self.logs(self.variant).body)
        packaging.write({'qty': 10})
        self.assertEqual(len(self.logs(self.variant)), 1)
        self.template.write({'packaging_ids': [Command.delete(packaging.id)]})
        self.assertIn('Видалено упаковку', self.logs(self.template)[-1].body)
        self.assertEqual(len(self.logs(self.variant)), 1)

    def test_supplier_inline_noop_and_delete(self):
        supplier = self.env['res.partner'].create({'name': 'Постачальник автологу'})
        self.variant.write({'seller_ids': [Command.create({
            'partner_id': supplier.id, 'price': 50, 'company_id': False,
        })]})
        line = self.template.seller_ids
        self.assertIn('Додано рядок постачальника', self.logs(self.variant).body)
        line.write({'price': 70})
        self.assertIn('Ціна: було «50.0», стало «70.0»', self.logs(self.template).body)
        line.write({'price': 70})
        self.variant.write({'seller_ids': [Command.delete(line.id)]})
        self.assertEqual(len(self.logs(self.template)), 1)
        self.assertIn('Видалено рядок постачальника', self.logs(self.variant)[-1].body)

    def test_attribute_line_lifecycle_and_price_extra(self):
        attribute = self.env['product.attribute'].create({'name': 'Колір автологу', 'create_variant': 'no_variant'})
        red, blue = self.env['product.attribute.value'].create([
            {'name': name, 'attribute_id': attribute.id} for name in ('Червоний', 'Синій')
        ])
        self.template.write({'attribute_line_ids': [Command.create({
            'attribute_id': attribute.id, 'value_ids': [Command.set(red.ids)],
        })]})
        line = self.template.attribute_line_ids
        self.assertIn('Додано атрибут', self.logs(self.template).body)
        line.write({'value_ids': [Command.link(blue.id)]})
        self.assertIn('Змінено атрибут', self.logs(self.template)[-1].body)
        line.product_template_value_ids.filtered(
            lambda value: value.product_attribute_value_id == blue
        ).write({'price_extra': 12})
        self.assertIn('Доплата: було «0.0», стало «12.0»', self.logs(self.template)[-1].body)
        line.unlink()
        self.assertIn('Видалено атрибут', self.logs(self.template)[-1].body)
        self.assertEqual(len(self.logs(self.template)), 4)
        self.assertFalse(self.logs(self.variant))

    def test_mass_write_and_cost_noise(self):
        second = self.env['product.template'].create({'name': 'Another audit product'})
        (self.template | second).write({'list_price': 25})
        self.assertEqual(len(self.logs(self.template)), 1)
        self.assertEqual(len(self.logs(second)), 1)
        self.variant.with_context(disable_auto_svl=True).write({'standard_price': 2})
        self.assertFalse(self.logs(self.variant))
        self.variant.write({'standard_price': 4})
        self.assertIn('Собівартість: було «2.0», стало «4.0»', self.logs(self.variant).body)
        self.assertEqual(self.logs(self.variant).product_autolog_company_id, self.env.company)

    def test_native_type_tracking(self):
        self.env.cr.precommit.run()
        self.template.write({'detailed_type': 'service'})
        self.env.flush_all()
        self.template._track_finalize()
        self.assertFalse(self.logs(self.template))
        self.assertTrue(self.template.message_ids.tracking_value_ids.filtered(
            lambda value: value.field_id.name == 'detailed_type' and value.new_value_char == 'Service'
        ))

    def test_bom_cost_recomputation_is_not_logged(self):
        if 'mrp.bom' not in self.env or not hasattr(self.variant, 'button_bom_cost'):
            self.skipTest('Requires the optional Manufacturing Accounting module')
        component = self.env['product.product'].create({'name': 'Cost component', 'standard_price': 7})
        self.env['mrp.bom'].create({
            'product_tmpl_id': self.template.id,
            'bom_line_ids': [Command.create({'product_id': component.id, 'product_qty': 2})],
        })
        self.variant.button_bom_cost()
        self.assertEqual(self.variant.standard_price, 14)
        self.assertFalse(self.logs(self.variant))

    def test_company_scope_and_standard_author(self):
        other_company = self.env['res.company'].create({'name': 'Other audit company'})
        user = new_test_user(
            self.env, login='autolog_company_user', groups='stock.group_stock_manager',
            company_id=other_company.id, company_ids=[Command.set(other_company.ids)],
        )
        editor = new_test_user(self.env, login='autolog_editor', groups='stock.group_stock_manager')
        self.variant.with_user(editor).write({'standard_price': 19})
        log = self.logs(self.variant)
        self.assertEqual(log.author_id, editor.partner_id)
        self.assertFalse(self.env['mail.message'].with_user(user).search([('id', '=', log.id)]))
        with self.assertRaises(AccessError):
            log.with_user(user).read(['body'])
        with self.assertRaises(AccessError):
            log.with_user(editor).write({'product_autolog_company_id': False})


@tagged('post_install', '-at_install')
class TestProductAutologThread(HttpCase):
    def test_product_toggle_fetch_keeps_manual_messages(self):
        self.authenticate('admin', 'admin')
        template = self.env['product.template'].create({'name': 'Product toggle test'})
        variant = template.product_variant_id
        self.env.cr.precommit.run()
        template.write({'detailed_type': 'service'})
        self.env.flush_all()
        template._track_finalize()
        technical = template.message_ids.filtered('tracking_value_ids')
        self.assertTrue(technical)
        for product in (template, variant):
            note = product.message_post(body='Автологи товару — звичайна нотатка', subtype_xmlid='mail.mt_note')
            product.write({'default_code': 'ROUTE-%s' % product.id})
            automatic = product.message_ids.filtered(
                lambda msg: msg.subtype_id == self.env.ref('product_vataga.mt_product_autolog')
            )
            self.assertEqual(len(automatic), 1)
            params = {
                'thread_model': product._name, 'thread_id': product.id, 'limit': 30,
            }
            for hide in (False, True, False):
                result = self.make_jsonrpc_request(
                    '/product_vataga/mail/thread/messages', {**params, 'hide_product_autologs': hide},
                )
                ids = [message['id'] for message in result['messages']]
                self.assertIn(note.id, ids)
                self.assertEqual(automatic.id in ids, not hide)
                if product == template:
                    self.assertEqual(bool(set(technical.ids) & set(ids)), not hide)
