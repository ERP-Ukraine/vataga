from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


@tagged('post_install', '-at_install')
class TestBomChangeLog(TransactionCase):
    """Test the report contract; standard PLM owns the BoM diff/workflow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Change = cls.env['mrp.eco.bom.change']
        cls.action = cls.env.ref('mrp_plm_vataga.action_bom_change_log')
        cls.domain = safe_eval(cls.action.domain)
        cls.finished, cls.component = cls.env['product.product'].create([
            {'name': 'PLM report finished'},
            {'name': 'PLM report component'},
        ])
        cls.bom = cls.env['mrp.bom'].create({
            'product_tmpl_id': cls.finished.product_tmpl_id.id,
            'product_qty': 1,
        })
        cls.eco_type = cls.env['mrp.eco.type'].create({'name': 'Vataga report test'})
        cls.eco = cls.env['mrp.eco'].create({
            'name': 'Vataga report ECO',
            'type_id': cls.eco_type.id,
            'type': 'bom',
            'product_tmpl_id': cls.finished.product_tmpl_id.id,
            'bom_id': cls.bom.id,
        })

    def _change(self, **values):
        return self.Change.create({
            'eco_id': self.eco.id,
            'product_id': self.component.id,
            'change_type': 'update',
            'old_product_qty': 5,
            'new_product_qty': 8,
            'old_uom_id': self.component.uom_id.id,
            'new_uom_id': self.component.uom_id.id,
            **values,
        })

    def _report(self, records):
        return self.Change.search(self.domain + [('id', 'in', records.ids)])

    def test_eco_and_technical_rows(self):
        self.assertEqual(self.domain, [('eco_id', '!=', False)])
        normal = self._change()
        technical = self._change(eco_id=False, eco_rebase_id=self.eco.id)
        self.assertEqual(self._report(normal | technical), normal)

    def test_related_bom_and_standard_values(self):
        for kind, old, new in [('add', 0, 4), ('remove', 7, 0), ('update', 5, 8)]:
            with self.subTest(change_type=kind):
                change = self._change(
                    change_type=kind, old_product_qty=old, new_product_qty=new,
                )
                row = self._report(change)
                self.assertEqual(row.vataga_bom_id, self.eco.bom_id)
                self.assertEqual(row.product_id, self.component)
                self.assertEqual(row.change_type, kind)
                self.assertEqual((row.old_product_qty, row.new_product_qty), (old, new))
                self.assertEqual(row.old_uom_id, self.component.uom_id)
                self.assertEqual(row.new_uom_id, self.component.uom_id)

    def test_zero_quantity_removal_visible(self):
        change = self._change(
            change_type='remove', old_product_qty=0, new_product_qty=0,
        )
        row = self._report(change)
        self.assertEqual(row, change)
        self.assertEqual(row.change_type, 'remove')
        self.assertEqual((row.old_product_qty, row.new_product_qty), (0, 0))

    def test_unfinished_eco_visible(self):
        for state in ('progress', 'rebase'):
            with self.subTest(state=state):
                self.eco.write({'state': state})
                change = self._change()
                self.assertEqual(change.eco_id.state, state)
                self.assertEqual(self._report(change), change)

    def test_standard_updates_and_deletion_reflected_without_duplicates(self):
        change = self._change()
        self.assertEqual(self._report(change), change)
        self.assertEqual(self._report(change), change)
        change.write({'new_product_qty': 9})
        self.assertEqual(self._report(change).new_product_qty, 9)
        change.unlink()
        self.assertFalse(self._report(change))

    def test_related_bom_follows_eco(self):
        change = self._change()
        other_bom = self.bom.copy()
        self.eco.bom_id = other_bom
        self.assertEqual(change.vataga_bom_id, other_bom)
        grouped = self.Change.read_group(
            [('id', '=', change.id)], ['vataga_bom_id'], ['vataga_bom_id'],
        )
        self.assertEqual(grouped[0]['vataga_bom_id'][0], other_bom.id)

    def test_readonly_views_and_action(self):
        tree = self.env.ref('mrp_plm_vataga.view_bom_change_log_tree')
        arch = etree.fromstring(tree.arch_db.encode())
        for flag in ('create', 'edit', 'delete'):
            self.assertEqual(arch.get(flag), '0')
        self.assertEqual(self.action.res_model, 'mrp.eco.bom.change')
        self.assertEqual(self.action.view_mode, 'tree')
        self.assertEqual(self.action.view_id, tree)
        self.assertEqual(
            self.action.search_view_id,
            self.env.ref('mrp_plm_vataga.view_bom_change_log_search'),
        )
        self.assertEqual(
            [field.get('name') for field in arch.findall('field')][:7],
            ['write_date', 'vataga_bom_id', 'product_id', 'change_type',
             'old_product_qty', 'new_product_qty', 'eco_id'],
        )
        change_type = arch.find("field[@name='change_type']")
        self.assertIsNotNone(change_type)
        self.assertNotEqual(change_type.get('optional'), 'hide')
        self.assertFalse(change_type.get('invisible'))
        self.assertFalse(change_type.get('column_invisible'))
        for name in ('vataga_bom_id', 'product_id', 'eco_id'):
            self.assertEqual(arch.find("field[@name='%s']" % name).get('widget'), 'many2one')
        search = etree.fromstring(self.action.search_view_id.arch_db.encode())
        self.assertEqual(
            safe_eval(search.find(".//filter[@name='group_date']").get('context')),
            {'group_by': 'write_date:day'},
        )

    def test_extension_has_no_separate_model_or_access_grants(self):
        field = self.Change._fields['vataga_bom_id']
        self.assertEqual(field.related, ('eco_id', 'bom_id'))
        self.assertTrue(field.store)
        self.assertTrue(field.readonly)
        self.assertFalse(field.compute_sudo)
        self.assertEqual(self.Change._table, 'mrp_eco_bom_change')
        self.assertFalse(self.env['ir.model.data'].search([
            ('module', '=', 'mrp_plm_vataga'),
            ('model', 'in', ['ir.model.access', 'ir.rule', 'ir.cron']),
        ]))
