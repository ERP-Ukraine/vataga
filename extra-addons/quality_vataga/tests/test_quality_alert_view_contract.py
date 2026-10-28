"""Portable XML contracts. These do not replace Enterprise ORM/view tests."""

import ast
from pathlib import Path
import re
import unittest

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]


class TestQualityAlertViewContract(unittest.TestCase):
    def setUp(self):
        self.stages = etree.parse(str(ROOT / 'data/quality_alert_board_stages.xml'))
        self.form = etree.parse(str(ROOT / 'views/quality_alert_views.xml'))

    def test_statusbar_and_data_register_the_same_five_stages(self):
        stage_ids = {'quality_vataga.' + value for value in self.stages.xpath('//record/@id')}
        domain = self.form.xpath("//xpath[@expr=\"//field[@name='stage_id']\"]/attribute[@name='domain']/text()")[0]
        self.assertEqual(len(stage_ids), 5)
        self.assertEqual(set(re.findall(r'%\(([^)]+)\)d', domain)), stage_ids)

    def test_new_stages_keep_noupdate_and_bracket_existing_board_order(self):
        self.assertEqual(self.stages.getroot().get('noupdate'), '1')
        for suffix, reference, offset in (
            ('new', 'board_analysis', '- 10'), ('closed', 'board_execution', '+ 10'),
        ):
            record = self.stages.xpath("//record[@id='quality_alert_stage_%s']" % suffix)[0]
            self.assertEqual(record.xpath("./field[@name='name']/text()"),
                             [{'new': 'Нове', 'closed': 'Закрито'}[suffix]])
            expression = record.xpath("./field[@name='sequence']/@eval")[0]
            self.assertIn('quality_vataga.quality_alert_stage_' + reference, expression)
            self.assertTrue(expression.endswith(offset))

    def test_statusbar_is_readonly_and_not_clickable(self):
        node = self.form.xpath("//xpath[@expr=\"//field[@name='stage_id']\"]")[0]
        self.assertEqual(node.xpath("./attribute[@name='readonly']/text()"), ['1'])
        options = ast.literal_eval(node.xpath("./attribute[@name='options']/text()")[0])
        self.assertFalse(options['clickable'])

    def test_actions_keep_board_approval_role_and_separate_manager_closure(self):
        for name in ('action_send_to_analysis', 'action_send_for_board_approval',
                     'action_approve_technical_board', 'action_close_alert'):
            nodes = self.form.xpath("//button[@name='%s']" % name)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get('type'), 'object')
        self.assertEqual(self.form.xpath("//button[@name='action_close_alert']/@groups"), ['quality.group_quality_manager'])
        self.assertEqual(self.form.xpath("//button[@name='action_approve_technical_board']/@groups"),
                         ['quality_vataga.group_quality_board_approval'])

    def test_snapshot_and_technical_board_notebook_are_preserved(self):
        fields = self.form.xpath("//field[@name='vataga_matrix_failure_snapshot']")
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0].get('readonly'), '1')
        self.assertEqual(fields[0].getparent().getparent().get('expr'), "//page[@name='miscellaneous']")
        self.assertEqual(len(self.form.xpath("//notebook[@name='technical_board_notebook']")), 1)

    def test_closed_stage_does_not_require_optional_enterprise_done_field(self):
        fields = self.stages.xpath("//record[@id='quality_alert_stage_closed']/field/@name")
        self.assertEqual(set(fields), {'name', 'sequence'})
        for name in ('vataga_closed_by_id', 'vataga_closed_at'):
            nodes = self.form.xpath("//field[@name='%s']" % name)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(nodes[0].get('readonly'), '1')
        self.assertEqual(self.form.xpath("//field[@name='full_control_line_ids']/@readonly"), ['vataga_closed_at'])

    def test_repair_page_uses_existing_main_notebook_and_protected_fields(self):
        repair = etree.parse(str(ROOT / 'views/quality_alert_repair_views.xml'))
        self.assertFalse(repair.xpath('//notebook'))
        page = repair.xpath("//page[@name='vataga_repair']")[0]
        self.assertEqual(page.getparent().get('expr'), "//page[@name='vataga_matrix_failure_history']")
        self.assertEqual(len(self.form.xpath(page.getparent().get('expr'))), 1)
        self.assertEqual(page.get('invisible'), "decision_type != 'repair'")
        for name in ('vataga_repair_done_by_id', 'vataga_repair_done_at',
                     'vataga_repair_note', 'vataga_repair_closed_check_id', 'vataga_repair_check_ids'):
            self.assertEqual(page.xpath(".//field[@name='%s']/@readonly" % name), ['1'])
        for tree in page.xpath('.//tree'):
            self.assertEqual([tree.get(name) for name in ('create', 'edit', 'delete')], ['0', '0', '0'])

    def test_repair_view_inheritance_anchors_match_module_views(self):
        repair = etree.parse(str(ROOT / 'views/quality_alert_repair_views.xml'))
        parents = {
            'quality_vataga.quality_alert_board_form': self.form,
            'quality_vataga.quality_check_view_form_measurement_matrix': etree.parse(str(ROOT / 'views/quality_check_views.xml')),
        }
        for record in repair.xpath('//record'):
            parent = parents[record.xpath("./field[@name='inherit_id']/@ref")[0]]
            for xpath in record.xpath("./field[@name='arch']/xpath"):
                # The QC patch targets the actual Enterprise sheet. Its parent
                # already uses that same anchor; Enterprise compilation is ORM-tested.
                if xpath.get('expr') == '//sheet':
                    self.assertTrue(parent.xpath('//xpath[@expr="//sheet"]'))
                else:
                    self.assertEqual(len(parent.xpath(xpath.get('expr'))), 1)

    def test_repair_wizard_declares_exact_scope_and_no_manual_result_controls(self):
        wizard = etree.parse(str(ROOT / 'wizard/quality_alert_repair_views.xml'))
        self.assertEqual(wizard.xpath("//field[@name='sample_ids']/@readonly"), ['1'])
        self.assertEqual(wizard.xpath('//button[@type="object"]/@name'), ['action_apply'])
        self.assertEqual(wizard.xpath('//button[@special="cancel"]/@string'), ['Закрити'])
        self.assertFalse(wizard.xpath('//field[@name="quality_state"]'))

    def test_scrap_waiting_page_is_addressed_readonly_and_not_an_execution_form(self):
        scrap = etree.parse(str(ROOT / 'views/quality_alert_scrap_views.xml'))
        self.assertFalse(scrap.xpath('//notebook|//button'))
        page = scrap.xpath("//page[@name='vataga_scrap']")[0]
        self.assertEqual(page.getparent().get('expr'), "//page[@name='vataga_matrix_failure_history']")
        self.assertEqual(len(self.form.xpath(page.getparent().get('expr'))), 1)
        self.assertEqual(page.get('invisible'), "decision_type != 'scrap'")
        for name in ('vataga_scrap_status', 'vataga_scrap_block_reason', 'vataga_failure_sample_ids'):
            self.assertEqual(page.xpath(".//field[@name='%s']/@readonly" % name), ['1'])
        self.assertFalse(page.xpath('.//field[@name="scrap_qty"]|.//field[@name="product_qty"]'))
        self.assertEqual(page.xpath('.//tree/field/@name'), ['controlled_unit_id', 'sample_number', 'sample_result'])


if __name__ == '__main__':
    unittest.main()
