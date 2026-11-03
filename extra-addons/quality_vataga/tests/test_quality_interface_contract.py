"""Portable UI/action regressions. These do not replace Enterprise ORM tests."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from lxml import etree

try:
    from .test_quality_defect_contract import shipped_class, UserError, AccessError
    from .test_quality_automatic_workorder_contract import Record, Rows
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError, AccessError
    from test_quality_automatic_workorder_contract import Record, Rows

ROOT = Path(__file__).resolve().parents[1]
JARGON = re.compile(r'\b(?:MO|WO|QC|QCP|Alert|QUNIT|QRUN|PASS|FAIL|Backorder|Snapshot|'
                    r'Work Orders?|RPC|ACL|Stock|MRP|checkpoint|pending)\b', re.I)


class TestMatrixAlertActionContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_check_alert_description.py',
                            'QualityCheckAlertDescription', {'action_open_matrix_quality_alert'})
        self.check = cls()
        check = self.check
        check.ensure_one = Mock()
        check.check_access_rights = Mock()
        check.check_access_rule = Mock()
        check._get_current_matrix_failure_data = Mock(return_value=[{'failure': True}])
        check._matrix_alert_request_key = lambda: 'same-evidence'
        check.x_control_stage, check._name, check.id, check.ids = 'in_process', 'quality.check', 7, [7]
        check._fields, check.product_id = {}, False
        self.alerts = NS(
            check_access_rights=Mock(), search=Mock(return_value=NS(id=False)),
            _fields={'check_id': NS(type='many2one', comodel_name='quality.check')},
        )
        class Environment:
            context = {'default_name': 'Unrelated form', 'lang': 'uk_UA'}
            def __getitem__(env, name):
                if name == 'quality.alert':
                    return self.alerts
                raise AssertionError(name)
            def ref(env, xmlid):
                # Reading any stored action (or using sudo) would fail here.
                if xmlid != 'quality_control.quality_alert_view_form':
                    raise AccessError('Stored actions are inaccessible')
                return NS(id=99)
        check.env = Environment()

    def test_opens_inline_form_with_normal_permissions_and_clean_defaults(self):
        action = self.check.action_open_matrix_quality_alert()
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'quality.alert')
        self.assertEqual(action['views'], [(99, 'form')])
        self.assertFalse(action['res_id'])
        self.assertEqual(action['context']['default_check_id'], 7)
        self.assertTrue(action['context']['quality_matrix_alert'])
        self.assertNotIn('default_name', action['context'])
        self.alerts.check_access_rights.assert_called_once_with('create')
        self.check.check_access_rule.assert_called_once_with('read')

    def test_repeated_open_targets_the_existing_alert(self):
        self.alerts.search.return_value = NS(id=25)
        self.assertEqual(self.check.action_open_matrix_quality_alert()['res_id'], 25)

    def test_other_control_stages_and_repaired_checks_are_rejected(self):
        for stage in ('incoming', 'final'):
            self.check.x_control_stage = stage
            with self.assertRaises(UserError):
                self.check.action_open_matrix_quality_alert()
        self.check.x_control_stage = 'in_process'
        self.check._get_current_matrix_failure_data.return_value = []
        with self.assertRaises(UserError):
            self.check.action_open_matrix_quality_alert()

    def test_stale_standard_button_uses_matrix_action_only_for_in_process(self):
        class Parent:
            def do_alert(self):
                return 'standard-action'
        cls = shipped_class('models/quality_check_alert_description.py',
            'QualityCheckAlertDescription', {'do_alert'}, parent=Parent)
        check = cls()
        check.ensure_one = Mock()
        check.action_open_matrix_quality_alert = Mock(return_value='matrix-action')
        for stage in ('incoming', 'final'):
            check.x_control_stage = stage
            self.assertEqual(check.do_alert(), 'standard-action')
        check.x_control_stage = 'in_process'
        self.assertEqual(check.do_alert(), 'matrix-action')

    def test_inherited_standard_buttons_keep_their_original_non_process_conditions(self):
        patch = etree.parse(str(ROOT / 'views/quality_check_views.xml'))
        base = etree.fromstring(b'''<form><header>
            <button name="do_alert" invisible="quality_state != 'pass'"/>
            <button name="do_alert" invisible="quality_state != 'fail'"/>
            </header></form>''')
        for xpath in patch.xpath("//xpath[attribute[@name='invisible']]"):
            for button in base.xpath(xpath.get('expr')):
                for attribute in xpath:
                    button.set(attribute.get('name'), button.get(attribute.get('name'))
                               + attribute.get('separator') + attribute.get('add'))
        for state in ('none', 'pass', 'fail'):
            for stage in ('incoming', 'in_process', 'final'):
                values = {'quality_state': state, 'x_control_stage': stage}
                visible = [b for b in base.xpath('//button')
                           if not eval(b.get('invisible'), {'__builtins__': {}}, values)]
                self.assertEqual(len(visible), int(stage != 'in_process' and state != 'none'))

    def test_duplicate_constraint_rejects_identical_requests_only(self):
        tree = ast.parse((ROOT / 'models/quality_alert.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'QualityAlert')
        assignment = next(n for n in cls.body if isinstance(n, ast.Assign)
                          and n.targets[0].id == '_sql_constraints')
        _, constraint, _ = ast.literal_eval(assignment.value)[0]
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE alert (check_id INTEGER, vataga_matrix_alert_key TEXT, ' + constraint + ')')
        db.execute("INSERT INTO alert VALUES (7, 'same')")
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO alert VALUES (7, 'same')")
        db.execute("INSERT INTO alert VALUES (8, 'same')")
        db.execute("INSERT INTO alert VALUES (7, 'changed')")
        db.execute('INSERT INTO alert VALUES (7, NULL)')
        db.execute('INSERT INTO alert VALUES (7, NULL)')

    def test_request_key_uses_raw_evidence_and_preserves_language_independence(self):
        cls = shipped_class('models/quality_check_alert_description.py',
            'QualityCheckAlertDescription', {'_matrix_alert_request_key'}, hashlib=hashlib, json=json)
        check = cls()
        check.ensure_one = Mock()
        value = Record(id=11, result='fail', numeric_value=11, boolean_value=False, string_value=False)
        check.sample_ids = Rows([Record(id=1, visual_result='yes', measurement_value_ids=Rows([value]))])
        key = check._matrix_alert_request_key()
        check._get_current_matrix_failure_data = Mock(side_effect=AssertionError('No translated text'))
        self.assertEqual(check._matrix_alert_request_key(), key)
        value.numeric_value = 12
        self.assertNotEqual(check._matrix_alert_request_key(), key)


class TestUkrainianInterfaceContract(unittest.TestCase):
    def assert_user_text(self, value, location):
        # Real references such as QUNIT/2026/00001 are document identifiers.
        text = re.sub(r'%\([^)]+\)[a-z]|\b(?:QUNIT|QRUN)/[^\s]+', '', value)
        self.assertIsNone(JARGON.search(text), (location, value))

    def test_python_user_messages_labels_and_history_have_no_technical_jargon(self):
        for folder in ('models', 'wizard', 'scripts'):
            for path in (ROOT / folder).glob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        if re.search('[А-Яа-яІіЇїЄє]', node.value):
                            self.assert_user_text(node.value, (path.name, node.lineno))
                    if isinstance(node, ast.keyword) and node.arg in ('string', 'help'):
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                            self.assert_user_text(node.value.value, (path.name, node.lineno))

    def test_xml_visible_text_has_no_technical_jargon(self):
        for folder in ('views', 'wizard', 'security', 'data', 'static/src'):
            for path in (ROOT / folder).rglob('*.xml'):
                for element in etree.parse(str(path)).iter():
                    for key in ('string', 'help', 'title', 'placeholder', 'confirm'):
                        if element.get(key):
                            self.assert_user_text(element.get(key), (path.name, element.sourceline))
                    if element.text and (re.search('[А-Яа-яІіЇїЄє]', element.text)
                                         or element.tag in ('option', 'button', 'p', 'div', 'span', 'strong')):
                        self.assert_user_text(element.text, (path.name, element.sourceline))

    def test_interpolated_message_keys_still_match_arguments(self):
        for path in (ROOT / 'models').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_'
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str) and node.keywords
                        and all(keyword.arg for keyword in node.keywords)):
                    expected = set(re.findall(r'%\(([^)]+)\)', node.args[0].value))
                    self.assertEqual(expected, {keyword.arg for keyword in node.keywords},
                                     (path.name, node.lineno))

    def test_matrix_status_labels_are_ukrainian_and_state_keys_stay_technical(self):
        source = (ROOT / 'static/src/components/measurement_matrix/measurement_matrix.js').read_text(encoding='utf-8')
        self.assertIn('result === "pass"', source)
        self.assertIn('result === "fail"', source)
        self.assertIn('return "Придатний"', source)
        self.assertIn('return "Непридатний"', source)
        template = etree.parse(str(ROOT / 'static/src/components/measurement_matrix/measurement_matrix.xml'))
        for key, label in (('pass', 'Придатний'), ('fail', 'Непридатний')):
            options = template.xpath("//option[@value='%s']" % key)
            self.assertTrue(options)
            self.assertTrue(all(option.text.strip() == label for option in options))


if __name__ == '__main__':
    unittest.main()
