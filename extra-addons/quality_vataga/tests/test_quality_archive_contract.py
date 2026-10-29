"""Portable schema preservation checks against the installed 17.0.2.45 contract."""
import ast
import csv
import hashlib
from pathlib import Path
import unittest

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
# Includes computed definitions too: this cleanup must not alter field schemas.
FIELD_CONTRACTS = {
    'models/quality_stock_posting.py': '44c4322a63b6eb0cfe3533134e9b10c603575d5b40ee7a3f8a497804c616f8ef',
    'models/quality_unit_release.py': '164893b5a67fee0a4c23ad2723d8898334e0afeeca1790b160076769e4161dbb',
    'models/quality_unit_release_readiness.py': '6dc26e6e33deb5b7b8cf7bc3e448a0f642961b2d0686ed4659ca164d620e5d33',
    'models/quality_unit_stock_release.py': '1ce52354f934697da19079bfa8546bae3df872e0bbf887cbe35e202fee689165',
    'models/stock_posting_core.py': '384fba5ad3ce2e31aa31c9eaa579ec9e8b24c8f1ce624d1fd2e2575df8993782',
    'models/stock_posting_valuation.py': '709af3cdf3ec56fe8483e90ad2534bc3ac8aaf781896df3f8ecb727588508a07',
    'wizard/quality_stock_timer_binding.py': '2240b052a536498a45db1ac1957e15ffe9bfc4169e7bbf7df36fd75b974d7215',
    'wizard/quality_unit_release_wizard.py': '4b052c3d164d3fea6ccd2df3f91a3f30436a2cd7ecffe92d98b7f8b39ffc3edc',
    'wizard/quality_unit_stock_release_wizard.py': 'a31b2e017eb795ac5e4a0d7ef6a71ec4c73c5ddcd51209571ac8e1151ce6e62f',
}
MOVED_FIELDS = ('quality_release_mo_quantity_label', 'quality_release_scope_unit_id', 'quality_release_scope_check_id')


class TestQualityArchiveSchema(unittest.TestCase):
    def test_all_field_definitions_are_preserved(self):
        for name, expected in FIELD_CONTRACTS.items():
            with self.subTest(name=name):
                tree = ast.parse((ROOT / name).read_text(encoding='utf-8-sig'))
                fields = [ast.dump(node, include_attributes=False) for node in ast.walk(tree)
                          if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                          and isinstance(node.value.func, ast.Attribute)
                          and isinstance(node.value.func.value, ast.Name)
                          and node.value.func.value.id == 'fields'
                          and node.targets[0].id not in MOVED_FIELDS]
                self.assertEqual(hashlib.sha256('\n'.join(fields).encode()).hexdigest(), expected)

    def test_moved_fields_keep_the_original_schema(self):
        definitions = {}
        for name in ('models/quality_check_identity.py', 'models/quality_unit_release.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in MOVED_FIELDS:
                    self.assertNotIn(node.targets[0].id, definitions)
                    definitions[node.targets[0].id] = ast.dump(node, include_attributes=False)
        digest = hashlib.sha256('\n'.join(definitions[name] for name in MOVED_FIELDS).encode()).hexdigest()
        self.assertEqual(digest, 'a89cd74754d022b16fd280b3fbddc428020891063ebdce877d31140356efa136')
        self.assertFalse((ROOT / 'models/quality_release_evidence.py').exists())

    def test_archive_acl_rows_are_read_only(self):
        with (ROOT / 'security/ir.model.access.csv').open(encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        archive_models = {'model_' + name.replace('.', '_') for name in (
            'quality.unit.release.batch', 'quality.unit.release.line', 'quality.unit.release.event',
            'quality.unit.stock.release.plan', 'quality.stock.cost.scope', 'quality.stock.posting',
            'quality.stock.posting.line', 'quality.stock.cost.allocation', 'quality.stock.timer.binding',
            'quality.unit.release.wizard', 'quality.unit.release.wizard.line',
            'quality.unit.stock.release.wizard', 'quality.unit.stock.release.wizard.line',
            'quality.stock.timer.binding.wizard')}
        selected = [row for row in rows if row['model_id:id'] in archive_models]
        self.assertEqual({row['model_id:id'] for row in selected}, archive_models)
        for row in selected:
            self.assertEqual([row[name] for name in ('perm_read', 'perm_write', 'perm_create', 'perm_unlink')],
                             ['1', '0', '0', '0'], row['id'])

    def test_manifest_paths_and_relative_imports_resolve(self):
        manifest = ast.literal_eval((ROOT / '__manifest__.py').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], '17.0.2.52')
        for path in manifest['data']:
            self.assertTrue((ROOT / path).is_file(), path)
            self.assertNotIn(path, ('wizard/quality_unit_release_views.xml',
                'wizard/quality_unit_stock_release_views.xml', 'wizard/quality_stock_timer_binding_views.xml'))
        for path in ROOT.rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                targets = [base.joinpath(*node.module.split('.'))] if node.module else [base / a.name for a in node.names]
                for target in targets:
                    self.assertTrue(target.with_suffix('.py').is_file() or (target / '__init__.py').is_file(),
                                    '%s -> %s' % (path, target))

    def test_archive_views_are_readonly_and_child_xpath_survives(self):
        parent = etree.parse(str(ROOT / 'views/quality_unit_release_views.xml'))
        child = etree.parse(str(ROOT / 'views/quality_unit_stock_release_views.xml'))
        for tree in (parent, child):
            self.assertFalse(tree.xpath('//button|//widget'))
            for node in tree.xpath('//form|//tree'):
                self.assertEqual([node.get(name) for name in ('create', 'edit', 'delete')], ['0', '0', '0'])
        inherited = child.xpath("//record[@id='mrp_production_quality_unit_stock_release']//xpath")
        self.assertEqual(len(inherited), 1)
        self.assertEqual(inherited[0].get('expr'), "//page[@name='quality_unit_release']")
        self.assertEqual(len(parent.xpath(inherited[0].get('expr'))), 1)
        js = (ROOT / 'static/src/components/measurement_matrix/admission_buttons.js').read_text(encoding='utf-8')
        self.assertNotIn('QualityUnitReleaseButtons', js)
        for component in ('QualityUnitAdmissionButtons', 'QualityUnitTransferWorkorderButtons', 'QualityUnitExecutionButtons'):
            self.assertIn('export class ' + component, js)

    def test_removed_engine_has_no_active_methods_and_gate_stays_closed(self):
        methods = {node.name for path in (ROOT / 'models').glob('*.py')
                   for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig')))
                   if isinstance(node, ast.FunctionDef)}
        self.assertFalse(methods.intersection({'_post_one', '_freeze_incremental', '_freeze_from_executions',
            '_get_release_readiness', '_get_unit_stock_release_proposal', '_bind_shared_timer',
            '_native_accounting_facts', '_finish_named_stock_workorders'}))
        tree = ast.parse((ROOT / 'models/quality_stock_posting.py').read_text(encoding='utf-8-sig'))
        definitions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        gate = definitions['_require_enterprise_validation']
        self.assertEqual(len(gate.body), 1)
        self.assertEqual(gate.body[0].value.func.id, 'archive_disabled')
        api = definitions['_post_plan']
        self.assertEqual(len(api.body), 2)  # docstring + unconditional guard
        self.assertEqual(api.body[-1].value.func.attr, '_require_enterprise_validation')

    def test_transfer_continuation_delegates_to_the_fail_closed_guard(self):
        tree = ast.parse((ROOT / 'models/quality_stock_continuation.py').read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '_check_named_stock_history')
        self.assertEqual(len(method.body), 2)
        self.assertEqual(method.body[0].value.func.attr, '_check_no_posted_stock_history')
        methods = {node.name for node in ast.walk(ast.parse((ROOT / 'models/stock_posting_core.py').read_text(encoding='utf-8-sig')))
                   if isinstance(node, ast.FunctionDef)}
        self.assertIn('_check_no_posted_stock_history', methods)
        self.assertNotIn('production_sources', methods)
        self.assertNotIn('move_sources', methods)

    def test_inventory_has_no_schema_or_data_mutation_statements(self):
        tree = ast.parse((ROOT / 'scripts/quality_archive_inventory.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                statement = node.value.strip().upper()
                self.assertFalse(statement.startswith(('DROP ', 'DELETE ', 'UPDATE ', 'INSERT ', 'ALTER ', 'TRUNCATE ')), node.value)

    def test_migration_only_updates_ui_metadata(self):
        tree = ast.parse((ROOT / 'migrations/17.0.2.46/pre-migration.py').read_text(encoding='utf-8'))
        queries = [node.args[0].value for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'execute']
        self.assertEqual(len(queries), 2)
        self.assertTrue(queries[0].strip().startswith('UPDATE ir_ui_view SET active = FALSE'))
        self.assertTrue(queries[1].strip().startswith('UPDATE ir_model_data SET noupdate = TRUE'))
        for query in queries:
            self.assertNotIn('DELETE', query.upper())
            self.assertNotIn('DROP', query.upper())


if __name__ == '__main__':
    unittest.main()
