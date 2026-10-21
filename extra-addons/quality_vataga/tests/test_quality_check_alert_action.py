from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .test_quality_check_in_process import QualityCheckInProcessCase


@tagged('post_install', '-at_install')
class TestQualityCheckAlertAction(QualityCheckInProcessCase):
    def _filled_check(self, quantity=1):
        check = self._check(self._production(quantity))
        self._fill(check)
        return check

    def _numeric(self, check, sample, value):
        cell = sample.measurement_value_ids.filtered(
            lambda item: item.parameter_type == 'numeric',
        )
        return check.update_measurement_value(cell.id, {'numeric_input': value})

    def _failed_check(self):
        check = self._filled_check()
        self._numeric(check, check.sample_ids, '11')
        return check

    def _save_action(self, action, **values):
        return self.env['quality.alert'].with_context(action['context']).create({
            'name': 'Сповіщення інспектора',
            'description': '<p>Опис інспектора</p>', **values,
        })

    def _inspector(self, suffix):
        return self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Alert action ' + suffix,
            'login': 'quality_alert_action_' + suffix,
            'groups_id': [Command.set(self.env.ref('quality.group_quality_user').ids)],
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
        })

    def test_sorting_parameter_is_an_editable_text_field(self):
        field = self.env['quality.alert']._fields['x_sorting_parameter']
        self.assertEqual(field.type, 'text')
        self.assertFalse(field.readonly)
        self.assertFalse(field.copy)
        self.assertEqual(field.string, 'Параметри невідповідності')

    def test_button_availability_follows_current_failures(self):
        check = self._check(self._production(1))
        self.assertFalse(check.can_create_matrix_quality_alert)
        check.update_measurement_visual_result(check.sample_ids.id, 'no')
        self.assertTrue(check.can_create_matrix_quality_alert)
        self.assertEqual(check.quality_state, 'none')
        self._fill(check)
        self.assertFalse(check.can_create_matrix_quality_alert)
        self._numeric(check, check.sample_ids, '11')
        self.assertTrue(check.can_create_matrix_quality_alert)
        self._numeric(check, check.sample_ids, '5')
        self.assertFalse(check.can_create_matrix_quality_alert)

    def test_combined_views_include_button_and_editable_sorting_text(self):
        check_view = self.env['quality.check'].get_view(
            view_id=self.env.ref('quality_control.quality_check_view_form').id,
            view_type='form',
        )
        check_arch = etree.fromstring(check_view['arch'])
        buttons = check_arch.xpath("//header/button[@name='action_open_matrix_quality_alert']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get('string'), 'Створити сповіщення')
        self.assertEqual(buttons[0].get('invisible'), 'not can_create_matrix_quality_alert')
        self.assertTrue(check_arch.xpath("//field[@name='can_create_matrix_quality_alert']"))
        alert_view = self.env['quality.alert'].get_view(
            view_id=self.env.ref('quality_control.quality_alert_view_form').id,
            view_type='form',
        )
        alert_arch = etree.fromstring(alert_view['arch'])
        fields = alert_arch.xpath("//field[@name='x_sorting_parameter']")
        self.assertEqual(len(fields), 1)
        self.assertNotIn(fields[0].get('readonly'), ('1', 'True'))
        self.assertEqual(fields[0].get('nolabel'), '1')
        self.assertEqual(fields[0].get('colspan'), '2')
        self.assertTrue({'w-100', 'text-break'} <= set(fields[0].get('class', '').split()))
        group = fields[0].getparent()
        self.assertEqual(group.tag, 'group')
        self.assertEqual(group.get('col'), '2')
        self.assertIn('w-100', group.get('class', '').split())
        self.assertEqual(group.getparent().tag, 'sheet')

    def test_failure_snapshot_has_one_readonly_page_in_the_main_notebook(self):
        alert_view = self.env['quality.alert'].get_view(
            view_id=self.env.ref('quality_control.quality_alert_view_form').id,
            view_type='form',
        )
        arch = etree.fromstring(alert_view['arch'])
        snapshots = arch.xpath("//field[@name='vataga_matrix_failure_snapshot']")
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.get('readonly'), '1')
        self.assertEqual(snapshot.get('nolabel'), '1')
        self.assertTrue({'w-100', 'text-break'} <= set(snapshot.get('class', '').split()))
        pages = arch.xpath("//page[@name='vataga_matrix_failure_history']")
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page.get('string'), 'Історія невідповідностей')
        self.assertEqual(page.get('invisible'), 'not vataga_matrix_failure_snapshot')
        self.assertEqual(snapshot.getparent(), page)
        notebook = page.getparent()
        self.assertEqual(notebook.tag, 'notebook')
        self.assertEqual(notebook.getparent().tag, 'sheet')
        self.assertNotEqual(notebook.get('name'), 'technical_board_notebook')
        self.assertEqual(page.xpath('preceding-sibling::page[1]')[0].get('name'), 'miscellaneous')
        standard_pages = ['description', 'corrective_actions', 'preventive_actions', 'miscellaneous']
        self.assertEqual(
            [item.get('name') for item in notebook.findall('page')
             if item.get('name') in standard_pages],
            standard_pages,
        )
        board_notebooks = arch.xpath("//notebook[@name='technical_board_notebook']")
        self.assertEqual(len(board_notebooks), 1)
        self.assertEqual(board_notebooks[0].get('invisible'), "decision_type != 'full_control'")
        self.assertTrue(board_notebooks[0].xpath(
            "./page[@name='technical_board_full_control']//field[@name='full_control_line_ids']",
        ))
        self.assertFalse(arch.xpath(
            "//notebook[@name='technical_board_notebook']//field"
            "[@name='vataga_matrix_failure_snapshot']",
        ))

    def test_opening_and_cancelling_form_creates_nothing_and_keeps_check_open(self):
        check = self._failed_check()
        alerts = self.env['quality.alert']
        before = alerts.search_count([])
        messages = check.message_ids
        for _ in range(2):
            action = check.action_open_matrix_quality_alert()
            self.assertEqual(action['type'], 'ir.actions.act_window')
            self.assertEqual(action['res_model'], 'quality.alert')
            self.assertEqual(action['view_mode'], 'form')
            self.assertFalse(action['res_id'])
            self.assertEqual(action['views'], [(
                self.env.ref('quality_control.quality_alert_view_form').id, 'form',
            )])
            self.assertNotIn('default_vataga_matrix_failure_snapshot', action['context'])
            self.assertNotIn('default_x_sorting_parameter', action['context'])
        self.assertEqual(alerts.search_count([]), before)
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual(check.message_ids, messages)

    def test_save_receives_check_product_team_and_supported_production_links(self):
        check = self._failed_check()
        action = check.action_open_matrix_quality_alert()
        context = action['context']
        self.assertEqual(context['default_check_id'], check.id)
        self.assertEqual(context['default_product_id'], check.product_id.id)
        self.assertEqual(context['default_team_id'], check.team_id.id)
        alert = self._save_action(action)
        self.assertEqual(alert.check_id, check)
        self.assertEqual(alert.product_id, check.product_id)
        self.assertEqual(alert.team_id, check.team_id)
        for name in ('production_id', 'company_id', 'picking_id', 'lot_id', 'partner_id'):
            if name in alert._fields and name in check._fields:
                field = alert._fields[name]
                if field.type == 'many2one' and field.comodel_name == check[name]._name:
                    self.assertEqual(context['default_' + name], check[name].id or False)
                    self.assertEqual(alert[name], check[name])
        self.assertEqual(alert.name, 'Сповіщення інспектора')
        self.assertIn('Опис інспектора', alert.description)
        self.assertEqual(check.quality_state, 'none')

    def test_current_team_overrides_unrelated_context_defaults(self):
        check = self._failed_check()
        team = self.env['quality.alert.team'].create({'name': 'Команда поточної перевірки'})
        check.team_id = team
        action = check.with_context(
            default_team_id=self.team.id, default_name='Назва іншої картки',
            default_production_id=999999, default_description='Чужий опис',
            default_x_sorting_parameter='Чужі параметри',
            active_model='mrp.production', active_id=999999,
        ).action_open_matrix_quality_alert()
        context = action['context']
        self.assertEqual(context['default_team_id'], team.id)
        if 'production_id' in self.env['quality.alert']._fields:
            self.assertEqual(context.get('default_production_id'), check.production_id.id)
        self.assertNotIn('default_name', context)
        self.assertNotIn('default_description', context)
        self.assertNotIn('default_x_sorting_parameter', context)
        self.assertEqual(context['active_model'], 'quality.check')
        self.assertEqual(context['active_id'], check.id)
        self.assertEqual(self._save_action(action).team_id, team)

    def test_sorting_and_snapshot_contain_only_current_failures_and_preserve_manual_text(self):
        check = self._filled_check(2)
        repaired, failed = check.sample_ids
        self._numeric(check, repaired, '11')
        self._numeric(check, repaired, '5')
        self._numeric(check, failed, '12')
        check.update_measurement_visual_result(failed.id, 'no')
        action = check.action_open_matrix_quality_alert()
        alert = self._save_action(action, x_sorting_parameter='Ручна примітка інспектора')
        snapshot = check._format_matrix_failure_data(check._get_current_matrix_failure_data())
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)
        self.assertTrue(alert.x_sorting_parameter.startswith('Ручна примітка інспектора\n'))
        self.assertIn(snapshot, alert.x_sorting_parameter)
        self.assertNotIn('Зразок №1', alert.x_sorting_parameter)
        self.assertIn('Зразок №2', alert.x_sorting_parameter)
        self.assertIn('норма: 0 – 10', alert.x_sorting_parameter)
        self.assertIn('Причина:', alert.x_sorting_parameter)
        self.assertIn('візуальний контроль', alert.x_sorting_parameter)
        self.assertIn('Опис інспектора', alert.description)
        self.assertEqual(alert.description.count('Виявлені невідповідності'), 1)

    def test_failures_are_recomputed_at_save_after_form_was_opened(self):
        check = self._filled_check(2)
        first, second = check.sample_ids
        self._numeric(check, first, '11')
        action = check.action_open_matrix_quality_alert()
        self._numeric(check, first, '5')
        self._numeric(check, second, '12')
        alert = self._save_action(action)
        for text in (alert.x_sorting_parameter, alert.vataga_matrix_failure_snapshot, alert.description):
            self.assertNotIn('Зразок №1', text)
            self.assertIn('Зразок №2', text)
        self.assertEqual(check.quality_state, 'none')

    def test_manual_or_context_failure_lines_are_not_duplicated(self):
        check = self._failed_check()
        failure_text = check._format_matrix_failure_data(check._get_current_matrix_failure_data())
        action = check.action_open_matrix_quality_alert()
        manual = 'Ручний текст\n' + failure_text
        alert = self._save_action(action, x_sorting_parameter=manual)
        self.assertEqual(alert.x_sorting_parameter, manual)
        context = dict(action['context'], default_x_sorting_parameter=manual)
        from_context = self._save_action(dict(action, context=context))
        self.assertEqual(from_context.x_sorting_parameter, manual)
        original = (alert.x_sorting_parameter, alert.vataga_matrix_failure_snapshot, alert.description)
        for _ in range(2):
            check.action_open_matrix_quality_alert()
            alert.read(['x_sorting_parameter', 'vataga_matrix_failure_snapshot', 'description'])
        self.assertEqual(original, (
            alert.x_sorting_parameter, alert.vataga_matrix_failure_snapshot, alert.description,
        ))

    def test_manual_sorting_edits_and_historical_alerts_are_not_refreshed(self):
        historical = self.env['quality.alert'].create({
            'name': 'Історична картка', 'team_id': self.team.id,
            'x_sorting_parameter': 'Історичний ручний текст',
        })
        check = self._failed_check()
        alert = self._save_action(check.action_open_matrix_quality_alert())
        snapshot = alert.vataga_matrix_failure_snapshot
        alert.x_sorting_parameter = 'Уточнення інспектора після створення'
        self._numeric(check, check.sample_ids, '5')
        alert.read(['x_sorting_parameter'])
        self.assertEqual(alert.x_sorting_parameter, 'Уточнення інспектора після створення')
        self.assertEqual(alert.vataga_matrix_failure_snapshot, snapshot)
        self.assertEqual(historical.x_sorting_parameter, 'Історичний ручний текст')

    def test_action_rejects_no_current_failures_and_other_control_stages(self):
        check = self._filled_check()
        with self.assertRaises(UserError):
            check.action_open_matrix_quality_alert()
        for stage in ('incoming', 'final'):
            point = self.point.copy({'x_control_stage': stage, 'visual_sample_control_required': True})
            other = self._check(point=point)
            other.add_measurement_samples(1)
            other.update_measurement_visual_result(other.sample_ids.id, 'no')
            self.assertFalse(other.can_create_matrix_quality_alert)
            with self.assertRaises(UserError):
                other.action_open_matrix_quality_alert()

    def test_parent_read_record_rules_are_enforced(self):
        check = self._failed_check()
        inspector = self._inspector('read_denied')
        self.env['ir.rule'].create({
            'name': 'Alert action deny check reads',
            'model_id': self.env['ir.model']._get_id('quality.check'),
            'domain_force': "[('id', '=', 0)]", 'perm_read': True,
            'perm_create': False, 'perm_write': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError):
            check.with_user(inspector).action_open_matrix_quality_alert()

    def test_standard_alert_create_rules_are_enforced_on_save(self):
        check = self._failed_check()
        inspector = self._inspector('create_denied')
        action = check.with_user(inspector).action_open_matrix_quality_alert()
        self.env['ir.rule'].create({
            'name': 'Alert action deny alert creation',
            'model_id': self.env['ir.model']._get_id('quality.alert'),
            'domain_force': "[('id', '=', 0)]", 'perm_create': True,
            'perm_read': False, 'perm_write': False, 'perm_unlink': False,
        })
        with self.assertRaises(AccessError), self.cr.savepoint():
            self.env['quality.alert'].with_user(inspector).with_context(action['context']).create({
                'name': 'Заборонене створення',
            })
        self.assertEqual(check.quality_state, 'none')

    def test_saved_standard_alert_accepts_inspector_photo_attachment(self):
        check = self._failed_check()
        inspector = self._inspector('photo')
        action = check.with_user(inspector).action_open_matrix_quality_alert()
        alert = self.env['quality.alert'].with_user(inspector).with_context(action['context']).create({
            'name': 'Фото невідповідності',
        })
        photo = self.env['ir.attachment'].with_user(inspector).create({
            'name': 'defect.png', 'res_model': 'quality.alert', 'res_id': alert.id,
            'mimetype': 'image/png',
            'datas': b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
        })
        self.assertEqual(photo.res_id, alert.id)
        self.assertEqual(alert.check_id, check)
        self.assertEqual(check.quality_state, 'none')

    def test_incoming_sorting_text_and_alert_logic_remain_unchanged(self):
        point = self.point.copy({'x_control_stage': 'incoming', 'visual_sample_control_required': True})
        check = self._check(point=point)
        check.add_measurement_samples(1)
        check.update_measurement_visual_result(check.sample_ids.id, 'no')
        for state in ('none', 'fail'):
            if state == 'fail':
                check.do_fail()
            alert = self.env['quality.alert'].create({
                'name': 'Вхідний контроль', 'check_id': check.id, 'team_id': check.team_id.id,
                'description': '<p>Ручний опис</p>', 'x_sorting_parameter': 'Ручні вхідні параметри',
            })
            self.assertEqual(alert.x_sorting_parameter, 'Ручні вхідні параметри')
            self.assertFalse(alert.vataga_matrix_failure_snapshot)
            self.assertIn('Ручний опис', alert.description)
            self.assertEqual('Виявлені невідповідності' in alert.description, state == 'fail')

    def test_bulk_create_preserves_explicit_and_default_manual_sorting_text(self):
        check = self._failed_check()
        alerts = self.env['quality.alert'].with_context(
            default_check_id=check.id, default_x_sorting_parameter='Контекстний текст',
        ).create([
            {'name': 'Явний текст', 'team_id': self.team.id, 'x_sorting_parameter': 'Власний текст'},
            {'name': 'Контекстний текст', 'team_id': self.team.id},
            {'name': 'Явно порожній', 'team_id': self.team.id, 'x_sorting_parameter': False},
        ])
        self.assertTrue(alerts[0].x_sorting_parameter.startswith('Власний текст\n'))
        self.assertTrue(alerts[1].x_sorting_parameter.startswith('Контекстний текст\n'))
        self.assertNotIn('Контекстний текст', alerts[2].x_sorting_parameter)
        self.assertEqual(alerts[2].x_sorting_parameter, alerts[2].vataga_matrix_failure_snapshot)
