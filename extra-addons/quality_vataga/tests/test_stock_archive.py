"""Real Community stock / archival safeguards, only in disposable test DBs.

SQL below seeds historical JOURNAL METADATA because its ORM is now read-only.
Every stock move, line and SVL is created by public native MO completion;
no stock quantities, valuation rows or accounting entries are fabricated.
"""
import importlib.util
from pathlib import Path

from psycopg2.extras import Json
from psycopg2.errors import ReadOnlySqlTransaction

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .test_stock_release_native_contract import StockReleaseNativeCase


@tagged('post_install', '-at_install')
class TestStockArchive(StockReleaseNativeCase):
    def _history(self):
        production = self._production(quantity=1)
        production.qty_producing = 1
        production.move_raw_ids.write({'quantity': 2, 'picked': True})
        production.button_mark_done()
        finished = production.move_finished_ids.filtered(lambda m: m.state == 'done')
        raw = production.move_raw_ids.filtered(lambda m: m.state == 'done')
        self.assertEqual((len(finished), len(raw)), (1, 1))
        self.assertEqual((self._stock(), production.qty_produced), ((18, 1), 1))
        self.env.flush_all()
        # Full-module Enterprise tests have extra required QUNIT identities;
        # they exercise these models through test_quality_archive instead.
        if 'unit_id' in self.env['quality.stock.posting']._fields:
            self.skipTest('Historical native journal fixture runs in the Community archive probe only.')
        cr = self.env.cr
        cr.execute("""INSERT INTO quality_stock_cost_scope
            (production_id, company_id, policy, snapshot, frozen_by_id, frozen_at)
            VALUES (%s,%s,%s,%s,%s,NOW()) RETURNING id""",
            [production.id, production.company_id.id, 'equal_closed_members_v1', Json({'timers': []}), self.env.uid])
        scope = cr.fetchone()[0]
        cost = -sum(raw.stock_valuation_layer_ids.mapped('value'))
        cr.execute("""INSERT INTO quality_stock_posting
            (production_id,company_id,scope_id,unit_key,permission_key,request_key,
             finished_move_id,finished_line_id,component_cost,labour_cost,posted_by_id,posted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,NOW()) RETURNING id""",
            [production.id, production.company_id.id, scope, 'archive:%s' % production.id,
             'archive-permission:%s' % production.id, 'archive-request', finished.id,
             finished.move_line_ids.id, cost, self.env.uid])
        posting = self.env['quality.stock.posting'].browse(cr.fetchone()[0])
        for move, kind in [(raw, 'component'), (finished, 'finished')]:
            cr.execute("""INSERT INTO quality_stock_posting_line
                (posting_id,production_id,company_id,kind,move_line_id,move_id,valuation_layer_id,quantity,uom_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                [posting.id, production.id, production.company_id.id, kind, move.move_line_ids.id,
                 move.id, move.stock_valuation_layer_ids.id, move.quantity, move.product_uom.id])
        self.env.invalidate_all()
        return production, posting, raw, finished

    def test_all_archived_stock_models_reject_creation_and_old_context(self):
        for name in ('quality.stock.cost.scope', 'quality.stock.posting',
                     'quality.stock.posting.line', 'quality.stock.cost.allocation'):
            for context in ({}, {'quality_stock_kernel': True}, {'quality_stock_kernel': 'legacy'}):
                with self.subTest(model=name, context=context), self.assertRaises(UserError):
                    self.env[name].with_context(context).create({})

    def test_historical_receipt_sources_and_values_remain_readable(self):
        production, posting, raw, finished = self._history()
        data = posting.read(['production_id', 'finished_move_id', 'finished_line_id',
                             'component_cost', 'valuation_cost', 'line_ids'])[0]
        self.assertEqual(data['production_id'][0], production.id)
        self.assertEqual(data['finished_move_id'][0], finished.id)
        self.assertEqual(data['component_cost'], 20)
        self.assertEqual(data['valuation_cost'], sum(finished.stock_valuation_layer_ids.mapped('value')))
        self.assertEqual(set(posting.line_ids.move_id.ids), set((raw | finished).ids))
        self.assertEqual(posting.scope_id.read(['snapshot'])[0]['snapshot'], {'timers': []})

    def test_historical_journal_is_immutable(self):
        _, posting, _, _ = self._history()
        for record in (posting, posting.scope_id, posting.line_ids):
            with self.assertRaises(UserError):
                record.write({})
            with self.assertRaises(UserError):
                record.unlink()
        self.assertEqual(len(posting.line_ids), 2)

    def test_historical_moves_lines_genealogy_and_svl_are_protected(self):
        _, posting, raw, finished = self._history()
        genealogy = finished.move_line_ids.consume_line_ids.ids
        layers = posting.line_ids.valuation_layer_id
        values = layers.mapped('value')
        for record, vals in [(raw, {'quantity': 3}),
                             (finished.move_line_ids, {'consume_line_ids': [(5, 0, 0)]}),
                             (raw.move_line_ids, {'quantity': 3}),
                             (layers, {'value': 999})]:
            with self.subTest(model=record._name), self.assertRaises(UserError), self.env.cr.savepoint():
                record.write(vals)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            raw.move_line_ids.unlink()
        self.assertEqual(finished.move_line_ids.consume_line_ids.ids, genealogy)
        self.assertEqual(layers.mapped('value'), values)
        self.assertEqual(self._stock(), (18, 1))

    def test_repeat_posting_is_blocked_even_with_legacy_context(self):
        production, posting, _, finished = self._history()
        before = (posting.line_ids.ids, self._stock(), production.qty_produced)
        with self.assertRaises(UserError), self.env.cr.savepoint():
            production.with_context(quality_stock_kernel=True)._post_inventory()
        with self.assertRaises(UserError), self.env.cr.savepoint():
            finished.with_context(quality_stock_kernel=True)._action_done()
        self.assertEqual((posting.line_ids.ids, self._stock(), production.qty_produced), before)

    def _reader(self, login, groups):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login,
            'company_id': self.env.company.id, 'company_ids': [Command.set(self.env.company.ids)],
            'groups_id': [Command.set([self.env.ref(name).id for name in ['base.group_user'] + groups])],
        })

    def test_archival_read_requires_native_roles_and_cannot_grant_mutation(self):
        _, posting, _, _ = self._history()
        stock_user = self._reader('archive-stock-only', ['stock.group_stock_user'])
        reader = self._reader('archive-native-reader', ['stock.group_stock_user', 'mrp.group_mrp_user'])
        self.assertFalse(stock_user.has_group('mrp.group_mrp_user'))
        with self.assertRaises(AccessError):
            posting.with_user(stock_user).read(['component_cost'])
        self.assertEqual(posting.with_user(reader).read(['component_cost'])[0]['component_cost'], 20)
        for operation in ('create', 'write', 'unlink'):
            self.assertFalse(posting.with_user(reader).check_access_rights(operation, raise_exception=False))
        with self.assertRaises(UserError):
            posting.with_user(reader).write({'component_cost': 0})

    def test_hidden_source_mo_prevents_reading_archived_snapshot(self):
        production, posting, _, _ = self._history()
        reader = self._reader('archive-source-restricted', ['stock.group_stock_user', 'mrp.group_mrp_user'])
        self.env['ir.rule'].create({
            'name': 'Disposable source MO visibility',
            'model_id': self.env['ir.model']._get('mrp.production').id,
            'domain_force': "[('id', '!=', %s)]" % production.id,
        })
        with self.assertRaises(AccessError):
            posting.with_user(reader).read(['component_cost'])

    def test_ui_migration_is_idempotent_and_does_not_change_history(self):
        production, posting, _, _ = self._history()
        names = ('mrp_production_quality_unit_stock_release', 'quality_unit_release_wizard_form',
                 'quality_unit_stock_release_wizard_form', 'stock_timer_binding_wizard_form')
        views = self.env['ir.ui.view']
        for name in names:
            view = views.create({'name': name, 'model': 'mrp.production', 'arch': '<form><field name="name"/></form>'})
            self.env['ir.model.data'].create({'module': 'quality_vataga', 'name': name,
                'model': 'ir.ui.view', 'res_id': view.id, 'noupdate': False})
            views |= view
        untouched = self.env['ir.ui.view'].create({'name': 'unrelated archive test', 'model': 'mrp.production', 'arch': '<form><field name="name"/></form>'})
        path = Path(__file__).resolve().parents[1] / 'migrations/17.0.2.46/pre-migration.py'
        spec = importlib.util.spec_from_file_location('archive_metadata_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        before = (posting.read(), posting.line_ids.read(), production.qty_produced, self._stock())
        self.env.flush_all()
        migration.migrate(self.env.cr, '17.0.2.45')
        migration.migrate(self.env.cr, '17.0.2.45')
        self.env.invalidate_all()
        self.assertFalse(any(views.mapped('active')))
        self.assertTrue(untouched.active)
        for name in names[1:]:
            self.assertTrue(self.env['ir.model.data'].search([('module', '=', 'quality_vataga'), ('name', '=', name)]).noupdate)
        self.assertEqual((posting.read(), posting.line_ids.read(), production.qty_produced, self._stock()), before)

    def test_unposted_mo_continuation_guard_does_not_change_quantities(self):
        production = self._production()
        before = (production.state, production.product_qty, production.qty_producing,
                  production.qty_produced, self._stock(), production.move_raw_ids.read(),
                  production.move_finished_ids.read(), production.workorder_ids.mapped('state'))
        self.assertTrue(production._check_no_posted_stock_history())
        self.assertTrue(production._check_no_posted_stock_history())
        self.assertEqual((production.state, production.product_qty, production.qty_producing,
                          production.qty_produced, self._stock(), production.move_raw_ids.read(),
                          production.move_finished_ids.read(), production.workorder_ids.mapped('state')), before)

    def test_old_migration_handles_missing_scope_table_and_preserves_history(self):
        _, posting, _, _ = self._history()
        path = Path(__file__).resolve().parents[1] / 'migrations/17.0.2.41/pre-migration.py'
        spec = importlib.util.spec_from_file_location('archive_scope_legacy_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        before = posting.scope_id.read()
        migration.migrate(self.env.cr, '17.0.2.40')
        migration.migrate(self.env.cr, '17.0.2.40')
        self.assertEqual(posting.scope_id.read(), before)
        self.env.cr.execute('SHOW search_path')
        previous_path = self.env.cr.fetchone()[0]
        # Disposable transactional schema represents an older installation
        # without this table. No business table is dropped or renamed.
        with self.env.cr.savepoint():
            self.env.cr.execute('CREATE SCHEMA quality_archive_missing_table_test')
            try:
                self.env.cr.execute('SET LOCAL search_path TO quality_archive_missing_table_test')
                migration.migrate(self.env.cr, '17.0.2.37')
                self.env.cr.execute("SELECT to_regclass('quality_stock_cost_scope')")
                self.assertIsNone(self.env.cr.fetchone()[0])
            finally:
                self.env.cr.execute('SELECT set_config(%s, %s, true)', ['search_path', previous_path])

    def test_legacy_receipt_cannot_authorize_continuation(self):
        production, posting, _, _ = self._history()
        before = (self._stock(), production.qty_produced, posting.line_ids.ids)
        with self.assertRaises(UserError):
            production._check_no_posted_stock_history()
        self.assertEqual((self._stock(), production.qty_produced, posting.line_ids.ids), before)

    def test_native_posting_without_legacy_receipt_also_blocks_continuation(self):
        production = self._production(quantity=1)
        production.qty_producing = 1
        production.move_raw_ids.write({'quantity': 2, 'picked': True})
        production.button_mark_done()
        self.assertFalse(self.env['quality.stock.posting'].search([('production_id', '=', production.id)]))
        with self.assertRaises(UserError):
            production._check_no_posted_stock_history()
        self.assertEqual((self._stock(), production.qty_produced), ((18, 1), 1))

    def test_history_of_another_mo_does_not_block_unposted_mo(self):
        self._history()
        production = self._production()
        self.assertTrue(production._check_no_posted_stock_history())
        self.assertEqual(production.qty_produced, 0)

    @mute_logger('odoo.sql_db')
    def test_inventory_reads_real_foreign_keys_but_cannot_write(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/quality_archive_inventory.py'
        spec = importlib.util.spec_from_file_location('quality_archive_inventory_test', path)
        inventory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inventory)
        # A separate cursor sees committed schema, never test fixture rows.
        with self.registry.cursor() as readonly_cr:
            readonly_cr.execute('SET TRANSACTION READ ONLY')
            report = inventory.collect_inventory(readonly_cr, 'NO-SUCH-MO-INVENTORY-TEST')
            self.assertTrue(report['read_only'])
            self.assertFalse(report['destructive_cleanup_authorized'])
            self.assertFalse(report['mo_identity_unambiguous'])
            self.assertEqual(len(report['archive_models']), 14)
            self.assertEqual(report['all_nine_present_and_empty'],
                             all(row['rows'] == 0 for row in report['archive_models'] if not row['transient']))
            self.assertTrue(any(row['source_table'] == 'quality_stock_posting' and
                                row['target_table'] == 'quality_stock_cost_scope' for row in report['foreign_keys']))
            with self.assertRaises(ReadOnlySqlTransaction), readonly_cr.savepoint(flush=False):
                readonly_cr.execute('UPDATE quality_stock_posting SET component_cost=component_cost')
