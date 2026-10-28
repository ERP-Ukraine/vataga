from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.quality_alert import APPROVAL_GROUP


@tagged('post_install', '-at_install')
class TestQualityTechnicalBoard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.board_group = cls.env.ref(APPROVAL_GROUP)
        cls.test_group = cls.env.ref('quality.group_quality_user')
        cls.manager_group = cls.env.ref('quality.group_quality_manager')
        cls.regular = cls._new_user('regular')
        cls.approver = cls._new_user('approver', cls.board_group)
        cls.other = cls._new_user('other', cls.board_group)
        cls.manager = cls._new_user('manager', cls.manager_group)
        cls.team = cls.env['quality.alert.team'].create({'name': 'Technical board tests'})
        cls.stages = {
            'new_stage': cls.env.ref('quality_vataga.quality_alert_stage_new'),
            'analysis_stage': cls.env.ref('quality_vataga.quality_alert_stage_board_analysis'),
            'approval_stage': cls.env.ref('quality_vataga.quality_alert_stage_board_approval'),
            'execution_stage': cls.env.ref('quality_vataga.quality_alert_stage_board_execution'),
            'closed_stage': cls.env.ref('quality_vataga.quality_alert_stage_closed'),
        }

    @classmethod
    def _new_user(cls, suffix, group=None):
        groups = cls.test_group | (group or cls.env['res.groups'])
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Technical board ' + suffix,
            'login': 'quality_board_test_' + suffix,
            'groups_id': [Command.set(groups.ids)],
            'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
        })

    def _alert(self, **values):
        vals = {
            'name': 'Technical board test',
            'team_id': self.team.id,
            'stage_id': self.stages['approval_stage'].id,
            'approval_user_id': self.approver.id,
            'decision_type': 'accept_as_is',
        }
        vals.update(values)
        if vals['stage_id'] == self.stages['approval_stage'].id:
            # Reach approval through the public action, then arrange invalid
            # decision/assignee states where a test specifically needs them.
            setup = dict(vals, stage_id=self.stages['new_stage'].id,
                         decision_type='accept_as_is', approval_user_id=self.approver.id)
            alert = self.env['quality.alert'].create(setup)
            alert.action_send_to_analysis()
            alert.action_send_for_board_approval()
            alert.write({key: vals[key] for key in ('decision_type', 'approval_user_id')})
            return alert
        if vals['stage_id'] == self.stages['analysis_stage'].id:
            alert = self.env['quality.alert'].create(dict(vals, stage_id=self.stages['new_stage'].id))
            alert.action_send_to_analysis()
            return alert
        return self.env['quality.alert'].create(vals)

    def test_manual_stage_changes_fail_before_mutating_record(self):
        alert = self._alert(stage_id=self.stages['analysis_stage'].id).with_user(self.regular)
        for key in ('approval_stage', 'execution_stage'):
            with self.assertRaises(UserError):
                alert.write({'stage_id': self.stages[key].id, 'decision_type': 'full_control'})
            alert.invalidate_recordset()
            self.assertEqual(alert.stage_id, self.stages['analysis_stage'])
            self.assertEqual(alert.decision_type, 'accept_as_is')

    def test_send_then_approve_are_the_only_forward_transitions(self):
        alert = self._alert(stage_id=self.stages['analysis_stage'].id)
        alert.with_user(self.regular).action_send_for_board_approval()
        self.assertEqual(alert.stage_id, self.stages['approval_stage'])
        with self.assertRaises(UserError):
            alert.with_user(self.approver).write({'stage_id': self.stages['execution_stage'].id})
        self.assertEqual(alert.stage_id, self.stages['approval_stage'])
        alert.with_user(self.approver).action_approve_technical_board()
        self.assertEqual(alert.stage_id, self.stages['execution_stage'])
        for key in ('analysis_stage', 'approval_stage'):
            with self.assertRaises(UserError):
                alert.with_user(self.approver).write({'stage_id': self.stages[key].id})
            alert.invalidate_recordset()
            self.assertEqual(alert.stage_id, self.stages['execution_stage'])
        with self.assertRaises(UserError):
            alert.action_send_for_board_approval()

    def test_send_requires_decision_and_assignee(self):
        cases = (
            ({'decision_type': False}, 'Оберіть рішення Технічної ради.'),
            ({'approval_user_id': False}, 'Оберіть користувача, який має затвердити рішення.'),
        )
        for values, message in cases:
            alert = self._alert(stage_id=self.stages['analysis_stage'].id, **values)
            with self.assertRaisesRegex(UserError, message):
                alert.with_user(self.regular).action_send_for_board_approval()
            alert.invalidate_recordset()
            self.assertEqual(alert.stage_id, self.stages['analysis_stage'])

    def _line(self, alert, **values):
        vals = {'alert_id': alert.id, 'checked_qty': 10, 'good_qty': 8, 'bad_qty': 2}
        vals.update(values)
        return self.env['quality.alert.full.control.line'].with_user(self.regular).create(vals)

    def test_regular_user_cannot_approve_even_when_assigned(self):
        self.assertFalse(self.regular.has_group(APPROVAL_GROUP))
        alert = self._alert(approval_user_id=self.regular.id)
        with self.assertRaises(AccessError):
            alert.with_user(self.regular).action_approve_technical_board()
        self.assertFalse(alert.is_approved)

    def test_assigned_approver_records_audit_and_chatter(self):
        alert = self._alert()
        before = alert.message_ids
        alert.with_user(self.approver).action_approve_technical_board()
        self.assertTrue(alert.is_approved)
        self.assertEqual(alert.approved_by_id, self.approver)
        self.assertTrue(alert.approved_at)
        self.assertEqual(alert.stage_id, self.stages['execution_stage'])
        messages = (alert.message_ids - before).filtered(
            lambda msg: 'Рішення Технічної ради затверджено' in (msg.body or '')
        )
        self.assertEqual(len(messages), 1)
        self.assertIn(self.approver.name, messages.body)

    def test_other_approver_cannot_approve(self):
        with self.assertRaises(AccessError):
            self._alert().with_user(self.other).action_approve_technical_board()

    def test_missing_assignee(self):
        with self.assertRaises(UserError):
            self._alert(approval_user_id=False).with_user(self.approver).action_approve_technical_board()

    def test_missing_decision(self):
        with self.assertRaises(UserError):
            self._alert(decision_type=False).with_user(self.approver).action_approve_technical_board()

    def test_wrong_stage(self):
        alert = self._alert(stage_id=self.stages['analysis_stage'].id)
        self.assertFalse(alert.is_board_approval_stage)
        with self.assertRaises(UserError):
            alert.with_user(self.approver).action_approve_technical_board()

    def test_repeat_is_noop(self):
        alert = self._alert().with_user(self.approver)
        alert.action_approve_technical_board()
        audit = (alert.approved_by_id, alert.approved_at, alert.message_ids)
        alert.action_approve_technical_board()
        self.assertEqual(audit, (alert.approved_by_id, alert.approved_at, alert.message_ids))

    def test_revoked_role_cannot_approve(self):
        alert = self._alert()
        self.approver.write({'groups_id': [Command.set(self.test_group.ids)]})
        with self.assertRaises(AccessError):
            alert.with_user(self.approver).action_approve_technical_board()

    def test_cannot_forge_audit_fields(self):
        alert = self._alert()
        for vals in ({'is_approved': True}, {'approved_by_id': self.approver.id},
                     {'approved_at': fields.Datetime.now()}, {'is_approved': False}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                alert.with_user(self.approver).with_context(board_approval=True).write(vals)
        with self.assertRaises(AccessError):
            self._alert(is_approved=True)
        defaults = self.env['quality.alert'].with_context(
            default_is_approved=True, default_approved_by_id=self.approver.id,
            default_approved_at=fields.Datetime.now(),
        ).create({'team_id': self.team.id})
        self.assertFalse(defaults.is_approved)
        self.assertFalse(defaults.approved_by_id)
        self.assertFalse(defaults.approved_at)

    def test_cannot_skip_approval_by_stage_write_or_create(self):
        with self.assertRaises(UserError):
            self._alert().write({'stage_id': self.stages['execution_stage'].id})
        for stage in ('approval_stage', 'execution_stage', 'closed_stage'):
            created = self.env['quality.alert'].create({
                'team_id': self.team.id, 'stage_id': self.stages[stage].id,
            })
            self.assertEqual(created.stage_id, self.stages['new_stage'])
            self.assertFalse(created.is_approved)

    def test_approved_decision_and_assignee_are_immutable(self):
        alert = self._alert().with_user(self.approver)
        alert.action_approve_technical_board()
        for vals in ({'decision_type': 'return_supplier'}, {'approval_user_id': self.other.id}):
            with self.subTest(vals=vals), self.assertRaises(UserError):
                alert.write(vals)

    def test_real_quality_hierarchy(self):
        self.assertIn(self.test_group, self.board_group.implied_ids)
        self.assertIn(self.board_group, self.manager_group.implied_ids)
        self.assertIn(self.env.ref('base.group_user'), self.test_group.trans_implied_ids)
        self.assertEqual(self.board_group.category_id, self.test_group.category_id)
        self.assertTrue(self.approver.has_group('quality.group_quality_user'))
        self.assertTrue(self.manager.has_group(APPROVAL_GROUP))

    def test_approver_domain_uses_real_membership(self):
        alerts = self.env['quality.alert']
        domain = alerts._fields['approval_user_id'].domain(alerts)
        allowed = self.env['res.users'].search(domain)
        self.assertIn(self.approver, allowed)
        self.assertIn(self.manager, allowed)
        self.assertNotIn(self.regular, allowed)

    def test_default_stage_is_new(self):
        alert = self.env['quality.alert'].create({'team_id': self.team.id})
        self.assertEqual(alert.stage_id, self.stages['new_stage'])

    def test_create_and_context_cannot_skip_new_stage(self):
        alerts = self.env['quality.alert']
        for stage in self.stages.values():
            defaults = alerts.with_context(default_stage_id=stage.id).default_get(['stage_id'])
            self.assertEqual(defaults['stage_id'], self.stages['new_stage'].id)
            created = alerts.with_context(default_stage_id=stage.id).create({'team_id': self.team.id})
            self.assertEqual(created.stage_id, self.stages['new_stage'])
            explicit = alerts.create({'team_id': self.team.id, 'stage_id': stage.id})
            self.assertEqual(explicit.stage_id, self.stages['new_stage'])
        batch = alerts.create([{'team_id': self.team.id},
                               {'team_id': self.team.id, 'stage_id': self.stages['closed_stage'].id}])
        self.assertEqual(batch.stage_id, self.stages['new_stage'])

    def test_send_to_analysis_requires_new_and_does_not_require_decision(self):
        alert = self.env['quality.alert'].create({'team_id': self.team.id}).with_user(self.regular)
        with self.assertRaises(UserError):
            alert.action_send_for_board_approval()
        alert.action_send_to_analysis()
        self.assertEqual(alert.stage_id, self.stages['analysis_stage'])
        self.assertFalse(alert.is_approved)
        self.assertFalse(alert.vataga_closed_at)
        self.assertFalse(alert.decision_type)
        with self.assertRaises(UserError):
            alert.action_send_to_analysis()

    def test_close_is_explicit_manager_action_with_immutable_idempotent_audit(self):
        alert = self._alert()
        alert.with_user(self.approver).action_approve_technical_board()
        self.assertEqual(alert.stage_id, self.stages['execution_stage'])
        self.assertFalse(alert._is_managed_closed())
        self.assertTrue(alert._matrix_hold_is_open())
        with self.assertRaises(UserError):
            alert.write({'stage_id': self.stages['closed_stage'].id})
        with self.assertRaises(AccessError):
            alert.with_user(self.approver).action_close_alert()
        with self.assertRaises(AccessError):
            alert.with_user(self.regular).action_close_alert()
        before = alert.message_ids
        alert.with_user(self.manager).action_close_alert()
        self.assertEqual(alert.stage_id, self.stages['closed_stage'])
        self.assertEqual(alert.vataga_closed_by_id, self.manager)
        self.assertTrue(alert.vataga_closed_at)
        self.assertFalse(alert._matrix_hold_is_open())
        self.assertEqual(len((alert.message_ids - before).filtered(
            lambda message: 'Сповіщення закрито' in (message.body or ''))), 1)
        audit = alert.vataga_closed_by_id, alert.vataga_closed_at, alert.message_ids
        alert.with_user(self.manager).action_close_alert()
        self.assertEqual(audit, (alert.vataga_closed_by_id, alert.vataga_closed_at, alert.message_ids))
        with self.assertRaises(UserError):
            alert.unlink()
        for vals in ({'vataga_closed_at': False}, {'vataga_closed_by_id': self.regular.id},
                     {'stage_id': self.stages['new_stage'].id}, {'check_id': 999999}):
            with self.assertRaises((UserError, AccessError)):
                alert.with_user(self.manager).write(vals)
        copied = alert.copy()
        self.assertEqual(copied.stage_id, self.stages['new_stage'])
        self.assertFalse(copied.vataga_closed_at)
        self.assertFalse(copied.is_approved)

    def test_close_requires_execution_and_verified_disposition(self):
        for key in ('new_stage', 'analysis_stage', 'approval_stage'):
            alert = self._alert(stage_id=self.stages[key].id)
            with self.assertRaises(UserError):
                alert.with_user(self.manager).action_close_alert()
            self.assertFalse(alert.vataga_closed_at)
        for decision in ('return_supplier', 'full_control'):
            alert = self._alert(decision_type=decision)
            alert.with_user(self.approver).action_approve_technical_board()
            with self.assertRaises(UserError):
                alert.with_user(self.manager).action_close_alert()
            self.assertEqual(alert.stage_id, self.stages['execution_stage'])

    def test_closure_defaults_cannot_forge_audit(self):
        values = dict(vataga_closed_by_id=self.manager.id, vataga_closed_at=fields.Datetime.now())
        with self.assertRaises(AccessError):
            self.env['quality.alert'].create(dict(values, team_id=self.team.id))
        alert = self.env['quality.alert'].with_context(**{
            'default_' + key: value for key, value in values.items()
        }).create({'team_id': self.team.id})
        self.assertFalse(alert.vataga_closed_by_id)
        self.assertFalse(alert.vataga_closed_at)
        self.assertTrue(alert._matrix_hold_is_open())

    def test_fold_done_and_approval_do_not_release_hold_or_forge_closure(self):
        alert = self._alert()
        alert.with_user(self.approver).action_approve_technical_board()
        for name in ('fold', 'done'):
            if name in alert.stage_id._fields:
                alert.stage_id[name] = True
                self.assertTrue(alert._matrix_hold_is_open())
        # Even a closed-looking legacy/imported row has no managed audit.
        self.cr.execute('UPDATE quality_alert SET stage_id=%s WHERE id=%s',
                        [self.stages['closed_stage'].id, alert.id])
        alert.invalidate_recordset(['stage_id'])
        self.assertTrue(alert._matrix_hold_is_open())

    def test_historical_native_stage_keeps_its_existing_closure_semantics(self):
        stage = self.env.ref('quality.quality_alert_stage_3')
        alert = self.env['quality.alert'].create({'team_id': self.team.id})
        self.cr.execute('UPDATE quality_alert SET stage_id=%s WHERE id=%s', [stage.id, alert.id])
        alert.invalidate_recordset(['stage_id'])
        before = stage.read()
        expected_open = 'done' not in stage._fields or not stage.done
        self.assertEqual(alert._matrix_hold_is_open(), expected_open)
        self.assertEqual(alert.stage_id, stage)
        self.assertEqual(stage.read(), before)

    def test_close_respects_record_rule(self):
        alert = self._alert()
        alert.with_user(self.approver).action_approve_technical_board()
        self.env['ir.rule'].create({
            'name': 'Deny closure of this alert',
            'model_id': self.env['ir.model']._get_id('quality.alert'),
            'domain_force': "[('id', '!=', %s)]" % alert.id,
        })
        with self.assertRaises(AccessError):
            alert.with_user(self.manager).action_close_alert()
        self.assertFalse(alert.vataga_closed_at)

    def test_stage_domain_contains_only_board_stages(self):
        alerts = self.env['quality.alert']
        domain = alerts._fields['stage_id'].domain(alerts)
        allowed = self.env['quality.alert.stage'].search(domain)
        self.assertEqual(set(allowed.ids), {s.id for s in self.stages.values()})
        for index in range(4):
            self.assertNotIn(self.env.ref('quality.quality_alert_stage_%s' % index), allowed)

    def test_kanban_excludes_populated_legacy_stages_without_moving_alerts(self):
        standard = self.env.ref('quality.quality_alert_stage_0')
        before = standard.read(['name', 'sequence'])
        # A historical fixture, not a permitted new-alert API path.
        legacy = self.env['quality.alert'].create({'team_id': self.team.id})
        self.cr.execute('UPDATE quality_alert SET stage_id=%s WHERE id=%s', [standard.id, legacy.id])
        legacy.invalidate_recordset(['stage_id'])
        alert = self._alert(stage_id=self.stages['analysis_stage'].id)
        groups = self.env['quality.alert'].read_group(
            [('id', 'in', (legacy | alert).ids)], ['stage_id'], ['stage_id'],
        )
        self.assertEqual({row['stage_id'][0] for row in groups},
                         {stage.id for stage in self.stages.values()})
        self.assertEqual(legacy.stage_id, standard)
        self.assertEqual(standard.read(['name', 'sequence']), before)
        self.assertIn(legacy, self.env['quality.alert'].search([('id', '=', legacy.id)]))

    def test_quality_manager_can_approve_when_assigned(self):
        alert = self._alert(approval_user_id=self.manager.id)
        alert.with_user(self.manager).action_approve_technical_board()
        self.assertEqual(alert.approved_by_id, self.manager)

    def test_stages_are_ordered_and_standard_stages_are_preserved(self):
        stages = [self.stages[key] for key in
                  ('new_stage', 'analysis_stage', 'approval_stage', 'execution_stage', 'closed_stage')]
        self.assertEqual(len(set(stage.id for stage in stages)), 5)
        for first, second in zip(stages, stages[1:]):
            self.assertLess(first.sequence, second.sequence)
        standard = [self.env.ref('quality.quality_alert_stage_%s' % i) for i in range(4)]
        self.assertFalse(set(s.id for s in standard) & set(s.id for s in stages))
        self.assertEqual(stages[2].with_context(lang=None).name, 'На затвердженні')

    def test_upgrade_renames_only_board_approval_stage(self):
        import os
        import runpy
        from odoo.modules.module import get_module_path

        standard = self.env['quality.alert.stage'].browse([
            self.env.ref('quality.quality_alert_stage_%s' % index).id
            for index in range(4)
        ])
        before = standard.read(['name', 'sequence'])
        approval = self.stages['approval_stage']
        approval.with_context(lang=None).name = 'Previous board approval label'
        script = runpy.run_path(os.path.join(
            get_module_path('quality_vataga'), 'migrations', '17.0.2.23', 'post-migration.py',
        ))
        script['migrate'](self.cr, '17.0.2.22')
        script['migrate'](self.cr, '17.0.2.22')
        self.assertEqual(approval.with_context(lang=None).name, 'На затвердженні')
        self.assertEqual(standard.read(['name', 'sequence']), before)

    def test_registered_inherited_view(self):
        from lxml import etree
        view = self.env.ref('quality_vataga.quality_alert_board_form')
        self.assertEqual(view.inherit_id, self.env.ref('quality_control.quality_alert_view_form'))
        self.assertTrue(view.active)
        arch = self.env['quality.alert'].with_user(self.approver).get_view(
            view_id=view.inherit_id.id, view_type='form')['arch']
        tree = etree.fromstring(arch.encode())
        from odoo.tools.safe_eval import safe_eval
        stage_node = tree.xpath("//field[@name='stage_id']")[0]
        self.assertEqual(stage_node.get('widget'), 'statusbar')
        self.assertEqual(stage_node.get('readonly'), '1')
        self.assertFalse(safe_eval(stage_node.get('options'))['clickable'])
        allowed = self.env['quality.alert.stage'].search(safe_eval(stage_node.get('domain')))
        self.assertEqual(set(allowed.ids), {stage.id for stage in self.stages.values()})
        self.assertTrue(tree.xpath("//button[@name='action_approve_technical_board']"))
        self.assertTrue(tree.xpath("//button[@name='action_send_to_analysis']"))
        self.assertTrue(tree.xpath("//field[@name='full_control_line_ids']"))
        regular_arch = self.env['quality.alert'].with_user(self.regular).get_view(
            view_id=view.inherit_id.id, view_type='form')['arch']
        self.assertNotIn('action_approve_technical_board', regular_arch)
        self.assertNotIn('action_close_alert', regular_arch)
        manager_arch = self.env['quality.alert'].with_user(self.manager).get_view(
            view_id=view.inherit_id.id, view_type='form')['arch']
        self.assertIn('action_close_alert', manager_arch)

    def test_full_control_incremental_totals_and_defaults(self):
        alert = self._alert(decision_type='full_control')
        first = self._line(alert)
        second = self._line(alert, checked_qty=5, good_qty=4, bad_qty=1)
        self.assertEqual(first.inspector_id, self.regular)
        self.assertEqual(first.date, fields.Date.context_today(first))
        self.assertEqual((alert.full_control_checked_qty, alert.full_control_good_qty,
                          alert.full_control_bad_qty), (15, 12, 3))
        second.write({'checked_qty': 6, 'good_qty': 5})
        self.assertEqual(alert.full_control_checked_qty, 16)
        second.unlink()
        self.assertEqual(alert.full_control_checked_qty, 10)

    def test_quantity_constraints(self):
        alert = self._alert(decision_type='full_control')
        for values in ({'good_qty': 7}, {'checked_qty': -1}, {'good_qty': -1}, {'bad_qty': -1}):
            with self.subTest(values=values), self.assertRaises(ValidationError), self.cr.savepoint():
                self._line(alert, **values)
        line = self._line(alert, checked_qty=0.3, good_qty=0.1, bad_qty=0.2)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            line.write({'bad_qty': 1})

    def test_other_decision_preserves_lines_without_gating_workflow(self):
        alert = self._alert(decision_type='full_control')
        line = self._line(alert)
        alert.decision_type = 'return_supplier'
        alert.with_user(self.approver).action_approve_technical_board()
        self.assertTrue(alert.is_approved)
        self.assertEqual(alert.full_control_line_ids, line)

    def test_lines_and_approval_respect_parent_record_rule(self):
        alert = self._alert()
        line = self._line(alert)
        self.env['ir.rule'].create({
            'name': 'Technical board test restricted alert',
            'model_id': self.env['ir.model']._get_id('quality.alert'),
            'domain_force': "[('id', '!=', %s)]" % alert.id,
        })
        with self.assertRaises(AccessError):
            alert.with_user(self.approver).action_approve_technical_board()
        with self.assertRaises(AccessError):
            line.with_user(self.regular).read(['checked_qty'])
        with self.assertRaises(AccessError):
            line.write({'good_qty': 9, 'bad_qty': 1})
        with self.assertRaises(AccessError):
            line.unlink()
        with self.assertRaises(AccessError):
            self._line(alert)
        lines = self.env['quality.alert.full.control.line'].with_user(self.regular)
        self.assertFalse(lines.search([('id', '=', line.id)]))
        self.assertFalse(lines.read_group([('id', '=', line.id)], ['checked_qty:sum'], ['alert_id']))
