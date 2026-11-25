"""Read-only diagnostic safety and stable history comparison; no database."""
import ast
import importlib.util
from types import SimpleNamespace
import unittest
from support import ROOT

PATH = ROOT / 'scripts/production_install_diagnostic.py'
spec = importlib.util.spec_from_file_location('_vataga_install_diagnostic', PATH)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class TestInstallDiagnostic(unittest.TestCase):
    def test_exact_supplied_xml_is_used_by_diagnostic(self):
        for kind, metadata in zip(('form','tree'), diagnostic.STUDIO):
            fixture = (ROOT / 'tests/fixtures' / ('production_studio_%s.xml' % kind)).read_text(encoding='utf8')
            self.assertEqual(diagnostic.canonical_xml(fixture), diagnostic.canonical_xml(metadata[3]))

    def test_diagnostic_has_no_mutation_sql_sudo_or_addon_import(self):
        tree = ast.parse(PATH.read_text(encoding='utf8'))
        forbidden = {'write','create','unlink','sudo','execute','commit','rollback','button_install',
                     'button_upgrade','button_immediate_install','button_immediate_upgrade','open'}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', '')
                self.assertNotIn(name, forbidden)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = node.module if isinstance(node, ast.ImportFrom) else ' '.join(alias.name for alias in node.names)
                self.assertNotIn('approvals_purchase_vataga', module)

    def test_fingerprints_are_sorted_ids_batched_and_do_not_output_values(self):
        class Records:
            ids = [2,1]
            _fields = {'name': SimpleNamespace(store=True), 'dynamic': SimpleNamespace(store=False)}
            def __len__(self):
                return len(self.ids)
            def browse(self, ids):
                self.batch_ids = ids
                return self
            def read(self, names, load):
                self.names, self.load = names, load
                return [{'id': i, 'name': 'private source ' + str(i)} for i in self.batch_ids]
        records = Records()
        value = diagnostic.fingerprint(records, ['name','dynamic'], 100)
        self.assertEqual(value['count'], 2)
        self.assertEqual(value['fields'], ['id','name'])
        self.assertEqual(len(value['sha256']), 64)
        self.assertNotIn('private source', str(value))
        records.ids.reverse()
        self.assertEqual(value, diagnostic.fingerprint(records, ['name','dynamic'], 100))
        self.assertEqual(records.batch_ids, [1,2])
        self.assertIsNone(records.load)
        self.assertEqual(diagnostic.fingerprint(records, ['name'], 1)['status'], 'BLOCKED_VOLUME')
