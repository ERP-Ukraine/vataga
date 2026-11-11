import os
import runpy
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, new_test_user, tagged

from ..models.quality_check_sample import AUTOMATIC_SAMPLE_INITIALIZATION
from ..models.stock_quarantine import QUARANTINE_CONTEXT, QUARANTINE_OPERATION


@tagged('post_install', '-at_install')
class TestQualityCheckMeasurementMatrix(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': 'Мультиметри (тест)',
        })
        cls.alternative_category = cls.env[
            'maintenance.equipment.category'
        ].create({
            'name': 'Струмовимірювальні кліщі (тест)',
        })
        cls.other_category = cls.env[
            'maintenance.equipment.category'
        ].create({
            'name': 'Інша категорія (тест)',
        })
        cls.no_equipment_category = cls.env[
            'maintenance.equipment.category'
        ].create({
            'name': 'Самостійна перевірка (тест)',
            'requires_equipment_selection': False,
        })
        cls.numeric_parameter = cls.env[
            'quality.equipment.parameter'
        ].create({
            'name': 'Напруга',
            'parameter_type': 'numeric',
            'unit': 'В',
        })
        cls.boolean_parameter = cls.env[
            'quality.equipment.parameter'
        ].create({
            'name': 'Цілісність кола',
            'parameter_type': 'boolean',
        })
        cls.string_parameter = cls.env[
            'quality.equipment.parameter'
        ].create({
            'name': 'Індикація',
            'parameter_type': 'string',
        })
        cls.category.applicable_parameter_ids = [Command.set([
            cls.numeric_parameter.id,
            cls.boolean_parameter.id,
            cls.string_parameter.id,
        ])]
        cls.alternative_category.applicable_parameter_ids = [
            Command.set([
                cls.numeric_parameter.id,
                cls.boolean_parameter.id,
                cls.string_parameter.id,
            ]),
        ]
        cls.no_equipment_category.applicable_parameter_ids = [
            Command.set([
                cls.numeric_parameter.id,
                cls.boolean_parameter.id,
                cls.string_parameter.id,
            ]),
        ]

        cls.equipment = cls.env['maintenance.equipment'].create({
            'name': 'Цифровий мультиметр',
            'category_id': cls.category.id,
            'serial_no': 'DMM-0002',
        })
        cls.second_equipment = cls.env['maintenance.equipment'].create({
            'name': 'Резервний мультиметр',
            'category_id': cls.category.id,
            'serial_no': 'DMM-0003',
        })
        cls.alternative_equipment = cls.env[
            'maintenance.equipment'
        ].create({
            'name': 'Струмовимірювальні кліщі',
            'category_id': cls.alternative_category.id,
            'serial_no': 'CLAMP-0002',
        })
        cls.other_equipment = cls.env['maintenance.equipment'].create({
            'name': 'Інше обладнання',
            'category_id': cls.other_category.id,
            'serial_no': 'OTHER-1',
        })
        cls.no_equipment_category_equipment = cls.env[
            'maintenance.equipment'
        ].create({
            'name': 'Технічний запис самостійної перевірки',
            'category_id': cls.no_equipment_category.id,
            'serial_no': 'NO-EQUIPMENT-1',
        })

        cls.quality_team = cls.env[
            'quality.alert.team'
        ].search([], limit=1)
        if not cls.quality_team:
            cls.quality_team = cls.env['quality.alert.team'].create({
                'name': 'Тестова команда якості матриці',
            })
        cls.test_type = cls.env[
            'quality.point.test_type'
        ].search([], limit=1)
        if not cls.test_type:
            cls.test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип матриці',
                'technical_name': 'quality_vataga_matrix_test',
            })
        cls.picking_type = cls.env[
            'stock.picking.type'
        ].search([], limit=1)
        cls.quality_point = cls.env['quality.point'].create({
            'title': 'Тестовий QCP матриці',
            'team_id': cls.quality_team.id,
            'test_type_id': cls.test_type.id,
            'picking_type_ids': [Command.set(cls.picking_type.ids)],
        })
        allowed_category_ids = [
            cls.category.id,
            cls.alternative_category.id,
        ]
        cls.numeric_line = cls.env[
            'quality.control.parameter.line'
        ].create({
            'quality_point_id': cls.quality_point.id,
            'sequence': 10,
            'control_type': 'instrumental',
            'equipment_category_ids': [
                Command.set(allowed_category_ids),
            ],
            'parameter_id': cls.numeric_parameter.id,
            'min_tolerance_input': '0',
            'max_tolerance_input': '10',
        })
        cls.boolean_line = cls.env[
            'quality.control.parameter.line'
        ].create({
            'quality_point_id': cls.quality_point.id,
            'sequence': 20,
            'control_type': 'functional',
            'equipment_category_ids': [
                Command.set(allowed_category_ids),
            ],
            'parameter_id': cls.boolean_parameter.id,
            'text_norm': 'Так',
        })
        cls.string_line = cls.env[
            'quality.control.parameter.line'
        ].create({
            'quality_point_id': cls.quality_point.id,
            'sequence': 30,
            'control_type': 'functional',
            'equipment_category_ids': [
                Command.set(allowed_category_ids),
            ],
            'parameter_id': cls.string_parameter.id,
            'text_norm': 'Працює коректно',
        })

    def _create_check(self):
        return self._create_check_for_point(self.quality_point)

    def test_alert_description_contains_only_failures_and_escapes_html(self):
        check = self._create_check()
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'no')
        numeric = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'numeric')
        boolean = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'boolean')
        text = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'string')
        check.update_measurement_value(numeric.id, {'numeric_input': '11'})
        check.update_measurement_value(boolean.id, {'boolean_value': 'yes'})
        check.update_measurement_value(text.id, {
            'string_value': '<script>alert(1)</script>', 'manual_result': 'fail',
        })
        description = str(check._get_technical_board_failure_description())
        self.assertIn(numeric.column_id.parameter_name, description)
        self.assertIn(numeric.failure_reason, description)
        self.assertIn('візуальний контроль', description)
        self.assertIn('11', description)
        self.assertNotIn(boolean.column_id.parameter_name, description)
        self.assertNotIn('<script>', description)
        self.assertIn('&lt;script&gt;', description)
        self.assertEqual(check.quality_state, 'none')

        check.do_fail()
        alerts = self.env['quality.alert'].create([
            {'check_id': check.id, 'team_id': self.quality_team.id,
             'description': '<p>Existing inspector note</p>'},
            {'check_id': check.id, 'team_id': self.quality_team.id},
            {'team_id': self.quality_team.id, 'description': '<p>Unrelated alert</p>'},
        ])
        for alert in alerts[:2]:
            self.assertIn(numeric.column_id.parameter_name, alert.description)
            self.assertIn(numeric.failure_reason, alert.description)
            self.assertIn('візуальний контроль', alert.description)
            self.assertNotIn('<script>', alert.description)
        self.assertIn('Existing inspector note', alerts[0].description)
        self.assertIn('Unrelated alert', alerts[2].description)
        self.assertNotIn('Виявлені невідповідності', alerts[2].description)

    def test_failed_alert_creation_does_not_require_write(self):
        point = self._create_visual_only_point('Create-only alert check')
        check = self._create_check_for_point(point)
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'no')
        check.do_fail()
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Create-only Quality inspector',
            'login': 'quality_board_create_only',
            'groups_id': [Command.set(self.env.ref('quality.group_quality_user').ids)],
            'company_id': self.env.company.id,
            'company_ids': [Command.set(self.env.company.ids)],
        })
        self.env['ir.rule'].create({
            'name': 'Test deny alert writes',
            'model_id': self.env['ir.model']._get_id('quality.alert'),
            'domain_force': "[('id', '=', 0)]",
            'perm_read': False, 'perm_create': False,
            'perm_write': True, 'perm_unlink': False,
        })
        alert = self.env['quality.alert'].with_user(user).create({
            'check_id': check.id, 'team_id': self.quality_team.id,
            'description': '<p>Original note</p>',
        })
        self.assertIn('Original note', alert.description)
        self.assertIn('візуальний контроль', alert.description)

    def test_alert_description_uses_default_check_and_preserves_default_text(self):
        point = self._create_visual_only_point('Alert description visual check')
        check = self._create_check_for_point(point)
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'no')
        check.do_fail()
        alert = self.env['quality.alert'].with_context(
            default_check_id=check.id,
            default_description='<p>Action default note</p>',
        ).create({'team_id': self.quality_team.id})
        self.assertEqual(alert.check_id, check)
        self.assertIn('Action default note', alert.description)
        self.assertIn('візуальний контроль', alert.description)

    def test_alert_without_failed_check_preserves_description(self):
        check = self._create_check()
        for values in ({}, {'check_id': check.id}):
            alert = self.env['quality.alert'].create(dict(
                values, team_id=self.quality_team.id,
                description='<p>Existing note</p>',
            ))
            self.assertIn('Existing note', alert.description)
            self.assertNotIn('Виявлені невідповідності', alert.description)

    def test_alert_description_empty_for_pending_and_passed_values(self):
        check = self._create_check()
        self.assertFalse(check._get_technical_board_failure_description())
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'yes')
        self.assertFalse(check._get_technical_board_failure_description())

    def _create_check_for_point(self, point, **values):
        return self.env['quality.check'].create({
            'point_id': point.id,
            'team_id': point.team_id.id,
            'test_type_id': point.test_type_id.id,
            **values,
        })

    def _add_sample(self, check):
        check.add_measurement_samples(1)
        return check.sample_ids

    def _create_quality_point(self, title):
        return self.env['quality.point'].create({
            'title': title,
            'team_id': self.quality_team.id,
            'test_type_id': self.test_type.id,
            'picking_type_ids': [Command.set(self.picking_type.ids)],
        })

    def _create_visual_only_point(self, title, required=True):
        point = self._create_quality_point(title)
        point.visual_sample_control_required = required
        return point

    def _create_boolean_point(self, title, categories):
        point = self._create_quality_point(title)
        line = self.env['quality.control.parameter.line'].create({
            'quality_point_id': point.id,
            'sequence': 10,
            'control_type': 'functional',
            'equipment_category_ids': [
                Command.set(categories.ids),
            ],
            'parameter_id': self.boolean_parameter.id,
            'text_norm': 'Так',
        })
        return point, line

    def _run_post_migration(self, migration_version, previous_version):
        migration_path = os.path.join(
            get_module_path('quality_vataga'),
            'migrations',
            migration_version,
            'post-migration.py',
        )
        migration = runpy.run_path(migration_path)
        migration['migrate'](self.cr, previous_version)

    def test_new_check_matrix_data_uses_false_id(self):
        new_check = self.env['quality.check'].new({})

        self.assertEqual(
            new_check.measurement_matrix_data,
            {'quality_check_id': False},
        )

    def _create_matrix_production(self, planned_quantity, bom=None):
        product = bom.product_id if bom else self.env['product.product'].create({
            'name': 'Виріб для автоматичної вибірки',
            'type': 'consu',
        })
        picking_type = self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id),
            ('manu_type_id.quality_quarantine_location_id', '=', False),
        ], limit=1).manu_type_id
        production = self.env['mrp.production'].create({
            'product_id': product.id,
            'product_uom_id': product.uom_id.id,
            'product_qty': planned_quantity,
            'picking_type_id': picking_type.id,
            'bom_id': bom.id if bom else False,
        })
        production.action_confirm()
        return production

    def _create_lot_matrix_production(self, planned_quantity=5, tracking='lot'):
        product = self.env['product.product'].create({
            'name': 'Виріб з партійним обліком',
            'type': 'product',
            'tracking': tracking,
        })
        workcenter = self.env['mrp.workcenter'].create({
            'name': 'Дільниця міжопераційного контролю',
        })
        bom = self.env['mrp.bom'].create({
            'product_id': product.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'operation_ids': [
                Command.create({
                    'name': name,
                    'sequence': sequence,
                    'workcenter_id': workcenter.id,
                })
                for sequence, name in [(10, 'Виготовлення'), (20, 'Контроль')]
            ],
        })
        return self._create_matrix_production(planned_quantity=planned_quantity, bom=bom)

    def test_control_stage_is_copied_from_point_at_creation(self):
        point = self.quality_point
        incoming = self._create_check()
        point.x_control_stage = 'in_process'
        in_process = self._create_check_for_point(
            point, x_control_stage='incoming',
        )
        point.x_control_stage = 'final'
        final = self._create_check()

        self.assertEqual(incoming.x_control_stage, 'incoming')
        self.assertEqual(in_process.x_control_stage, 'in_process')
        self.assertEqual(final.x_control_stage, 'final')
        with self.assertRaises(UserError):
            in_process.write({'x_control_stage': 'incoming'})
        for check in incoming | final:
            self.assertTrue(check.add_measurement_samples(2)['manual_samples'])
            self.assertEqual(len(check.sample_ids), 2)
            check.remove_measurement_samples(1)
            self.assertEqual(len(check.sample_ids), 1)

    def test_in_process_samples_use_actual_quantity_and_keep_results(self):
        production = self._create_matrix_production(planned_quantity=5)
        production.qty_producing = 5
        finished_move = production.move_finished_ids
        finished_move.write({'quantity': 5, 'picked': False})
        self.quality_point.x_control_stage = 'in_process'
        check = self._create_check_for_point(
            self.quality_point, production_id=production.id,
        )
        data = check.get_measurement_matrix_data()
        self.assertFalse(data['samples'])
        self.assertFalse(data['manual_samples'])
        self.assertIn('ще не зареєстровано', data['automatic_sample_message'])
        with self.assertRaises(ValidationError):
            check.do_pass()

        production.qty_producing = 3
        finished_move.write({'quantity': 3, 'picked': True})
        picked_line = finished_move.move_line_ids.filtered('picked')
        picked_line.copy({
            'move_id': finished_move.id, 'quantity': 2, 'picked': False,
        })
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(finished_move.quantity, 5)
        data = check.get_measurement_matrix_data()
        self.assertEqual(
            [sample['sample_number'] for sample in data['samples']], [1, 2, 3],
        )
        self.assertFalse(data['automatic_sample_message'])
        with self.assertRaises(UserError):
            check.add_measurement_samples(1)
        with self.assertRaises(UserError):
            check.remove_measurement_samples(1)
        with self.assertRaises(UserError):
            check.sample_ids[-1:].sudo().unlink()
        with self.assertRaises(UserError):
            self.env['quality.check.sample'].sudo().with_context(
                quality_vataga_sample_initialization=True,
            ).create({'quality_check_id': check.id, 'sample_number': 4})

        samples = check.sample_ids
        first_value = samples[0].measurement_value_ids.filtered(
            lambda value: value.parameter_type == 'numeric',
        )
        check.update_measurement_value(first_value.id, {'numeric_input': '7'})
        picked_line.quantity = 2
        reopened = check.get_measurement_matrix_data()
        self.assertEqual([sample['id'] for sample in reopened['samples']], samples.ids)
        self.assertEqual(check.sample_ids, samples)
        self.assertEqual(first_value.numeric_value, 7)

        with self.assertRaisesRegex(ValidationError, 'Оберіть'):
            check.do_pass()
        check.equipment_selection_ids.with_context(
            quality_vataga_equipment_selection=True,
        ).write({'equipment_ids': [Command.set(self.equipment.ids)]})
        with self.assertRaisesRegex(ValidationError, 'Заповніть'):
            check.write({'quality_state': 'pass'})
        values = {
            'numeric': {'numeric_input': '7'},
            'boolean': {'boolean_value': 'yes'},
            'string': {'string_value': 'Справно', 'manual_result': 'pass'},
        }
        for sample in samples:
            for value in sample.measurement_value_ids:
                check.update_measurement_value(value.id, values[value.parameter_type])
        with self.assertRaisesRegex(ValidationError, 'Заповніть візуальний'):
            check.do_pass()
        for sample in samples:
            check.update_measurement_visual_result(sample.id, 'yes')
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')
        self.assertEqual(check.sample_ids, samples)

    def test_in_process_check_can_be_opened_after_production_completion(self):
        production = self._create_matrix_production(planned_quantity=3)
        point = self._create_visual_only_point('Автовибірка після випуску')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        production.qty_producing = 3
        production.move_finished_ids.write({'quantity': 3, 'picked': True})
        check.get_measurement_matrix_data()
        for sample in check.sample_ids:
            check.update_measurement_visual_result(sample.id, 'yes')
        check.do_pass()
        sample_ids = check.sample_ids.ids
        production.with_context(skip_backorder=True).button_mark_done()

        self.assertEqual(production.state, 'done')
        reopened = check.get_measurement_matrix_data()
        self.assertEqual([sample['id'] for sample in reopened['samples']], sample_ids)
        self.assertFalse(reopened['editable'])
        check_after = self._create_check_for_point(point, production_id=production.id)
        self.assertEqual(len(check_after.get_measurement_matrix_data()['samples']), 3)

    def test_in_process_reads_confirmed_enterprise_registration(self):
        checks = self.env['quality.check']
        if 'finished_product_sequence' not in checks._fields:
            self.skipTest('Shop Floor is not installed')
        register_type = self.env['quality.point.test_type'].search([
            ('technical_name', '=', 'register_production'),
        ], limit=1)
        self.assertTrue(register_type)
        production = self._create_matrix_production(planned_quantity=5)
        workorder = self.env['mrp.workorder'].create({
            'name': 'Контроль операції',
            'production_id': production.id,
            'workcenter_id': self.env['mrp.workcenter'].create({
                'name': 'Дільниця автоматичної вибірки',
            }).id,
            'product_uom_id': production.product_uom_id.id,
        })
        point = self._create_visual_only_point('Міжопераційний контроль')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(
            point, production_id=production.id, workorder_id=workorder.id,
        )
        registration = checks.create({
            'production_id': production.id,
            'workorder_id': workorder.id,
            'product_id': production.product_id.id,
            'team_id': point.team_id.id,
            'test_type_id': register_type.id,
            'qty_done': 3,
        })
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        registration.action_next()
        self.assertEqual(registration.quality_state, 'pass')
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(len(check.get_measurement_matrix_data()['samples']), 3)

    def test_in_process_reads_previous_workorder_registration_for_same_lot(self):
        checks = self.env['quality.check']
        if 'finished_product_sequence' not in checks._fields:
            self.skipTest('Shop Floor is not installed')
        register_type = self.env['quality.point.test_type'].search([
            ('technical_name', '=', 'register_production'),
        ], limit=1)
        self.assertTrue(register_type)
        production = self._create_lot_matrix_production()
        product = production.product_id
        first, second = production.workorder_ids.sorted(
            lambda workorder: workorder.operation_id.sequence,
        )
        self.assertEqual(second.blocked_by_workorder_ids, first)
        lot, other_lot = self.env['stock.lot'].create([
            {'name': name, 'product_id': product.id, 'company_id': production.company_id.id}
            for name in ['Партія контролю', 'Інша партія']
        ])
        point = self._create_visual_only_point('Контроль після виготовлення')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(
            point, production_id=production.id, workorder_id=second.id,
        )
        registration_vals = {
            'production_id': production.id,
            'workorder_id': first.id,
            'product_id': product.id,
            'team_id': point.team_id.id,
            'test_type_id': register_type.id,
        }
        checks.create(dict(
            registration_vals, lot_id=other_lot.id, qty_done=4,
        )).action_next()
        checks.create(dict(
            registration_vals, lot_id=lot.id, qty_done=2,
            finished_product_sequence=1,
        )).action_next()
        registration = checks.create(dict(
            registration_vals, lot_id=lot.id, qty_done=3,
        ))
        first.button_start(bypass=True)
        production.qty_producing = 3
        self.assertFalse(check.lot_id)
        self.assertEqual(production.lot_producing_id, lot)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        registration.action_next()
        self.assertEqual(registration.quality_state, 'pass')
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(production.qty_producing, 3)
        self.assertEqual(production.qty_produced, 0)
        data = check.get_measurement_matrix_data()
        self.assertEqual([row['sample_number'] for row in data['samples']], [1, 2, 3])
        sample_ids = check.sample_ids.ids
        check.update_measurement_visual_result(sample_ids[0], 'yes')
        reopened = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['id'] for row in reopened], sample_ids)
        self.assertEqual(reopened[0]['visual_result'], 'yes')

    def test_shop_floor_dialog_builds_next_operation_matrix_before_stock_posting(self):
        checks = self.env['quality.check']
        if 'finished_product_sequence' not in checks._fields:
            self.skipTest('Shop Floor is not installed')
        production = self._create_lot_matrix_production()
        first, second = production.workorder_ids.sorted(
            lambda workorder: workorder.operation_id.sequence,
        )
        lot = self.env['stock.lot'].create({
            'name': 'Партія з діалогу Shop Floor',
            'product_id': production.product_id.id,
            'company_id': production.company_id.id,
        })
        point = self._create_visual_only_point('Контроль на наступній операції')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(
            point, production_id=production.id, workorder_id=second.id,
        )
        first.button_start(bypass=True)
        self.assertEqual(first.state, 'progress')
        self.assertEqual(second.state, 'pending')
        self.assertEqual(production.qty_producing, 5)
        production.lot_producing_id = lot
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        # The built-in dialog saves the MO and calls this RPC.
        production.write({'qty_producing': 3, 'lot_producing_id': lot.id})
        production.set_qty_producing()
        self.assertFalse(check.lot_id)
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(first.qty_producing, 3)
        self.assertEqual(second.qty_producing, 3)
        self.assertFalse(checks.search_count([
            ('production_id', '=', production.id),
            ('test_type_id.technical_name', '=', 'register_production'),
        ]))
        data = check.get_measurement_matrix_data()
        self.assertEqual([row['sample_number'] for row in data['samples']], [1, 2, 3])
        sample_ids = check.sample_ids.ids
        for sample_id in sample_ids:
            check.update_measurement_visual_result(sample_id, 'yes')
        check.do_pass()
        production.with_context(skip_backorder=True).button_mark_done()

        self.assertEqual(production.state, 'done')
        self.assertEqual(production.qty_produced, 3)
        reopened = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['id'] for row in reopened], sample_ids)
        self.assertEqual([row['visual_result'] for row in reopened], ['yes'] * 3)
        check_after = self._create_check_for_point(
            point, production_id=production.id, workorder_id=second.id,
        )
        self.assertEqual(len(check_after.get_measurement_matrix_data()['samples']), 3)

    def test_shop_floor_full_batch_requires_confirmation_before_stock_posting(self):
        if 'finished_product_sequence' not in self.env['quality.check']._fields:
            self.skipTest('Shop Floor is not installed')
        production = self._create_lot_matrix_production(planned_quantity=2)
        first, second = production.workorder_ids.sorted(
            lambda workorder: workorder.operation_id.sequence,
        )
        lot, other_lot = self.env['stock.lot'].create([
            {
                'name': name,
                'product_id': production.product_id.id,
                'company_id': production.company_id.id,
            }
            for name in ('Підтверджена партія', 'Інша партія')
        ])
        point = self._create_visual_only_point('Міжопераційний контроль повної партії')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(
            point, production_id=production.id, workorder_id=second.id,
        )
        first.button_start(bypass=True)
        production.write({'qty_producing': 2, 'lot_producing_id': lot.id})
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.set_qty_producing()
        self.assertEqual(check._get_in_process_sample_quantity(), (2, False))
        production.lot_producing_id = other_lot
        self.assertFalse(check.get_measurement_matrix_data()['samples'])
        production.lot_producing_id = lot
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.set_qty_producing()
        self.assertEqual(production.qty_produced, 0)
        samples = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['sample_number'] for row in samples], [1, 2])
        self.assertEqual(check.get_measurement_matrix_data()['samples'], samples)

    def test_shop_floor_confirms_full_batch_for_check_without_operation(self):
        production = self._create_lot_matrix_production(planned_quantity=4)
        first = production.workorder_ids.sorted('sequence')[:1]
        self.assertTrue(first)
        first.button_start(bypass=True)
        lot, other_lot = self.env['stock.lot'].create([
            {
                'name': name,
                'product_id': production.product_id.id,
                'company_id': production.company_id.id,
            }
            for name in ('Підтверджена партія MO', 'Інша партія MO')
        ])
        production.write({'qty_producing': 4, 'lot_producing_id': lot.id})
        point = self._create_visual_only_point('Міжопераційний контроль на рівні MO')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        wrong_lot_check = self._create_check_for_point(
            point, production_id=production.id, lot_id=other_lot.id,
        )
        self.assertFalse(check.workorder_id)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.set_qty_producing()
        self.assertEqual(production.quality_registered_quantity, 4)
        self.assertEqual(production.quality_registered_lot_id, lot)
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(wrong_lot_check.get_measurement_matrix_data()['samples'])
        samples = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['sample_number'] for row in samples], [1, 2, 3, 4])
        self.assertEqual(check.automatic_sample_lot_id, lot)
        self.assertEqual(check.get_measurement_matrix_data()['samples'], samples)

    def test_shop_floor_registers_partial_batch_before_last_operation(self):
        production = self._create_lot_matrix_production(planned_quantity=5)
        first, last = production.workorder_ids.sorted('sequence')
        lot, other_lot = self.env['stock.lot'].create([
            {
                'name': name,
                'product_id': production.product_id.id,
                'company_id': production.company_id.id,
            }
            for name in ('Партія часткового випуску', 'Змінена партія')
        ])
        point = self._create_visual_only_point('Контроль часткового випуску на рівні MO')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        production.write({'qty_producing': 3, 'lot_producing_id': lot.id})
        first.with_context(no_start_next=True, mrp_display=True).do_finish()
        self.assertEqual(first.state, 'done')
        self.assertTrue(production.quality_shop_floor_registration_required)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.set_qty_producing()
        self.assertFalse(production.quality_shop_floor_registration_required)
        production.qty_producing = 2
        self.assertTrue(production.quality_shop_floor_registration_required)
        production.qty_producing = 3
        production.set_qty_producing()
        production.lot_producing_id = other_lot
        self.assertTrue(production.quality_shop_floor_registration_required)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.lot_producing_id = lot
        production.set_qty_producing()
        last.with_context(no_start_next=True, mrp_display=True).do_finish()
        self.assertEqual(last.state, 'done')
        self.assertEqual(production.qty_produced, 0)
        self.assertFalse(production.quality_shop_floor_registration_required)
        samples = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['sample_number'] for row in samples], [1, 2, 3])
        self.assertEqual(check.automatic_sample_lot_id, lot)
        self.assertEqual(check.get_measurement_matrix_data()['samples'], samples)

    def test_shop_floor_confirms_full_batch_without_workorders(self):
        production = self._create_matrix_production(planned_quantity=4)
        point = self._create_visual_only_point('Міжопераційний контроль без операцій')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        production.qty_producing = 4
        self.assertFalse(production.workorder_ids)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        production.set_qty_producing()
        self.assertEqual(production.quality_registered_quantity, 4)
        self.assertEqual(production.qty_produced, 0)
        samples = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['sample_number'] for row in samples], [1, 2, 3, 4])
        self.assertEqual(check.get_measurement_matrix_data()['samples'], samples)

    def _register_from_mo_form(self, production):
        return production.with_context(
            quality_registration_quantity=production.qty_producing,
            quality_registration_lot_id=production.lot_producing_id.id or False,
        ).action_quality_register_production()

    def test_mo_form_registers_partial_batch_after_both_operations_finish(self):
        production = self._create_lot_matrix_production()
        point = self._create_visual_only_point('Контроль після реєстрації з форми MO')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        lots = self.env['stock.lot'].create([
            {'name': name, 'product_id': production.product_id.id, 'company_id': production.company_id.id}
            for name in ('Партія з форми MO', 'Інша партія з форми MO')
        ])
        lot, other_lot = lots
        production.write({'qty_producing': 3, 'lot_producing_id': lot.id})
        production.workorder_ids.button_finish()
        self.assertEqual(production.state, 'to_close')
        self.assertEqual(production.product_qty, 5)
        self.assertEqual(production.qty_producing, 3)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        moves = production.move_raw_ids | production.move_finished_ids
        move_data = moves.read(['state', 'picked', 'quantity', 'move_line_ids'])
        self._register_from_mo_form(production)
        self.assertEqual(production.quality_registered_quantity, 3)
        self.assertEqual(production.quality_registered_lot_id, lot)
        self.assertEqual(production.state, 'to_close')
        self.assertEqual(production.qty_produced, 0)
        self.assertEqual(production.move_raw_ids | production.move_finished_ids, moves)
        self.assertEqual(moves.read(['state', 'picked', 'quantity', 'move_line_ids']), move_data)

        production.lot_producing_id = other_lot
        self.assertFalse(production.quality_registered_quantity)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])
        with self.assertRaises(UserError):
            production.with_context(
                quality_registration_quantity=3, quality_registration_lot_id=lot.id,
            ).action_quality_register_production()
        production.lot_producing_id = lot
        self.assertFalse(check.get_measurement_matrix_data()['samples'])
        self._register_from_mo_form(production)
        samples = check.get_measurement_matrix_data()['samples']
        self.assertEqual([row['sample_number'] for row in samples], [1, 2, 3])
        self.assertEqual(check.automatic_sample_lot_id, lot)
        self._register_from_mo_form(production)
        self.assertEqual(check.get_measurement_matrix_data()['samples'], samples)
        self.assertEqual(check.quality_state, 'none')

    def test_mo_form_requires_explicit_confirmation_and_mrp_access_without_workorders(self):
        production = self._create_matrix_production(planned_quantity=4)
        production.qty_producing = 4
        self.assertFalse(production.quality_output_registration_available)
        with self.assertRaises(UserError):
            self._register_from_mo_form(production)
        point = self._create_visual_only_point('Контроль з форми MO без операцій')
        point.x_control_stage = 'in_process'
        check = self._create_check_for_point(point, production_id=production.id)
        self.assertFalse(check.get_measurement_matrix_data()['samples'])

        quality_user = new_test_user(
            self.env, login='matrix_registration_inspector', groups='quality.group_quality_user',
        )
        self.assertFalse(quality_user.has_group('mrp.group_mrp_user'))
        with self.assertRaises(AccessError):
            production.with_user(quality_user).with_context(
                quality_registration_quantity=4,
            ).action_quality_register_production()
        self.assertFalse(production.quality_registered_quantity)
        with self.assertRaises(UserError):
            production.action_quality_register_production()

        self._register_from_mo_form(production)
        self.assertEqual(production.quality_registered_quantity, 4)
        self.assertFalse(production.quality_registered_lot_id)
        self.assertEqual(production.qty_produced, 0)
        create_samples = type(check)._create_measurement_samples

        def check_snapshot_is_flushed(record, *args):
            record.env.cr.execute(
                'SELECT automatic_sample_count FROM quality_check WHERE id = %s',
                [record.id],
            )
            self.assertEqual(record.env.cr.fetchone()[0], 4)
            return create_samples(record, *args)

        with patch.object(type(check), '_create_measurement_samples', check_snapshot_is_flushed):
            self.assertEqual(len(check.get_measurement_matrix_data()['samples']), 4)

    def test_mo_form_excludes_serial_and_requires_lot(self):
        point = self._create_visual_only_point('Контроль з форми MO з відстеженням')
        point.x_control_stage = 'in_process'
        for tracking in ('serial', 'lot'):
            with self.subTest(tracking=tracking):
                production = self._create_lot_matrix_production(planned_quantity=1, tracking=tracking)
                check = self._create_check_for_point(point, production_id=production.id)
                production.qty_producing = 1
                self.assertEqual(production.quality_output_registration_available, tracking == 'lot')
                with self.assertRaises(UserError):
                    self._register_from_mo_form(production)
                self.assertFalse(production.quality_registered_quantity)
                self.assertFalse(check.get_measurement_matrix_data()['samples'])

    def test_point_without_matrix_configuration_stays_standard(self):
        point = self._create_visual_only_point(
            'Стандартна перевірка без custom matrix',
            required=False,
        )
        check = self._create_check_for_point(point)

        self.assertFalse(check.measurement_matrix_required)
        self.assertFalse(check.measurement_column_ids)
        self.assertFalse(check.equipment_selection_ids)
        self.assertFalse(check.sample_ids)
        self.assertFalse(check.can_initialize_measurement_matrix)
        check._validate_measurement_can_pass()
        with self.assertRaisesRegex(
            UserError,
            'матриця показників не потрібна',
        ):
            check.add_measurement_samples(1)

    def test_visual_only_check_has_matrix_without_dynamic_structure(self):
        point = self._create_visual_only_point(
            'Візуальна перевірка без показників',
        )
        check = self._create_check_for_point(point)

        self.assertTrue(check.measurement_matrix_required)
        self.assertFalse(check.measurement_column_ids)
        self.assertFalse(check.equipment_selection_ids)
        self.assertFalse(check.sample_ids)
        self.assertFalse(check.can_initialize_measurement_matrix)
        self.assertEqual(check.get_measurement_matrix_data()['columns'], [])
        with self.assertRaisesRegex(UserError, 'незмінним snapshot'):
            check.write({'measurement_matrix_required': False})

    def test_visual_only_samples_are_created_without_measurement_values(self):
        point = self._create_visual_only_point(
            'Кілька зразків візуального контролю',
        )
        check = self._create_check_for_point(point)

        matrix_data = check.add_measurement_samples(3)

        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2, 3])
        self.assertFalse(check.sample_ids.measurement_value_ids)
        self.assertEqual(len(matrix_data['samples']), 3)
        self.assertEqual(
            set(check.sample_ids.mapped('sample_result')),
            {'pending'},
        )
        self.assertFalse(check.measurement_matrix_complete)
        self.assertTrue(check.equipment_selection_complete)

    def test_visual_only_empty_result_blocks_pass_and_yes_allows_it(self):
        point = self._create_visual_only_point(
            'Успішний візуальний контроль',
        )
        check = self._create_check_for_point(point)
        sample = self._add_sample(check)

        self.assertEqual(sample.sample_result, 'pending')
        self.assertFalse(sample.is_complete)
        self.assertFalse(sample.has_failure)
        with self.assertRaisesRegex(
            ValidationError,
            'Заповніть візуальний контроль для всіх зразків',
        ):
            check.do_pass()

        check.update_measurement_visual_result(sample.id, 'yes')

        self.assertEqual(sample.sample_result, 'pass')
        self.assertTrue(sample.is_complete)
        self.assertFalse(sample.has_failure)
        self.assertTrue(check.measurement_matrix_complete)
        self.assertTrue(check.can_pass_measurement_check)
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')

    def _create_quarantine_order(self, *, with_check=True, controlled=True):
        warehouse_count = self.env['stock.warehouse'].search_count([
            ('code', '=like', 'Q13%'),
        ])
        warehouse = self.env['stock.warehouse'].create({
            'name': f'Склад перевірки карантину {warehouse_count}',
            'code': f'Q13{warehouse_count:02d}',
        })
        quarantine = self.env['stock.location'].with_context(
            **{QUARANTINE_CONTEXT: QUARANTINE_OPERATION},
        ).create({
            'name': 'Очікує перевірку якості', 'usage': 'internal',
            'location_id': warehouse.view_location_id.id,
            'company_id': warehouse.company_id.id, 'quality_quarantine': True,
        })
        picking_type = warehouse.manu_type_id
        picking_type.with_context(**{QUARANTINE_CONTEXT: QUARANTINE_OPERATION}).write({
            'quality_quarantine_location_id': quarantine.id,
        })
        product = self.env['product.product'].create({
            'name': 'Партія для перевірки карантину', 'type': 'product', 'tracking': 'lot',
        })
        workcenter = self.env['mrp.workcenter'].create({'name': 'Контроль партії'})
        bom = self.env['mrp.bom'].create({
            'product_id': product.id,
            'product_tmpl_id': product.product_tmpl_id.id,
            'operation_ids': [Command.create({'name': 'Контроль', 'workcenter_id': workcenter.id})],
        })
        point = self.env['quality.point']
        if controlled:
            point = point.create({
                'title': 'Обов’язковий міжопераційний контроль',
                'team_id': self.quality_team.id,
                'test_type_id': self.env['quality.point.test_type'].search([
                    ('technical_name', '=', 'passfail'),
                ], limit=1).id,
                'picking_type_ids': [Command.set(picking_type.ids)],
                'operation_id': bom.operation_ids.id,
                'product_ids': [Command.set(product.ids)],
                'x_control_stage': 'in_process',
                'visual_sample_control_required': True,
            })
        production = self.env['mrp.production'].create({
            'product_id': product.id, 'product_qty': 5, 'product_uom_id': product.uom_id.id,
            'picking_type_id': picking_type.id, 'bom_id': bom.id,
        })
        production.action_confirm()
        check = production.quality_quarantine_check_ids.filtered(lambda rec: rec.point_id == point)
        if with_check and controlled and not check:
            check = self._create_check_for_point(
                point, production_id=production.id, workorder_id=production.workorder_ids.id,
                product_id=product.id, company_id=production.company_id.id,
            )
        if not with_check:
            check.unlink()
            check = self.env['quality.check']
        lot = self.env['stock.lot'].create({
            'name': 'Тестова партія', 'product_id': product.id, 'company_id': production.company_id.id,
        })
        production.write({'lot_producing_id': lot.id, 'qty_producing': 5})
        return production, check, quarantine, warehouse.lot_stock_id

    def _pass_quarantine_check(self, check):
        data = check.get_measurement_matrix_data()
        self.assertTrue(data['samples'])
        for sample in data['samples']:
            check.update_measurement_visual_result(sample['id'], 'yes')
        check.do_pass()

    def test_quarantine_pending_and_pass_preserves_batch_and_releases_once(self):
        production, check, quarantine, stock = self._create_quarantine_order()
        product = production.product_id
        lot = production.lot_producing_id
        quant = self.env['stock.quant']
        source_move = production.move_finished_ids
        downstream = self.env['stock.move'].create({
            'name': 'Подальший рух', 'product_id': product.id, 'product_uom': product.uom_id.id,
            'product_uom_qty': 5, 'location_id': stock.id,
            'location_dest_id': self.env.ref('stock.stock_location_customers').id,
            'company_id': production.company_id.id,
            'move_orig_ids': [Command.link(source_move.id)],
        })
        downstream._action_confirm(merge=False)
        production.button_mark_done()
        receipt = production.move_finished_ids
        self.assertEqual(production.state, 'done')
        self.assertEqual(check.quality_state, 'none')
        self.assertEqual(production.quality_quarantine_status, 'pending')
        self.assertEqual(receipt.location_dest_id, quarantine)
        self.assertEqual(receipt.move_line_ids.lot_id, lot)
        self.assertEqual(receipt.quantity, 5)
        release = receipt.quality_release_move_ids
        self.assertEqual(downstream.move_orig_ids, release)
        downstream._action_assign()
        self.assertFalse(downstream.move_line_ids)
        self.assertFalse(quant._get_reserve_quantity(product, quarantine, 5, lot_id=lot))
        self.assertFalse(quant._get_reserve_quantity(product, quarantine.location_id, 5, lot_id=lot))
        self.assertEqual(quant._get_available_quantity(product, quarantine, lot_id=lot), 5)
        with self.assertRaises(UserError), self.cr.savepoint():
            release._action_done()
        with self.assertRaises(UserError), self.cr.savepoint():
            receipt.move_line_ids.write({'quantity': 1})
        with self.assertRaises(UserError), self.cr.savepoint():
            quant.search([('location_id', '=', quarantine.id)]).write({'inventory_quantity': 1})
        self.assertEqual(production.action_open_quarantine_checks()['res_model'], 'quality.check')

        self._pass_quarantine_check(check)
        self.assertEqual(production.quality_quarantine_status, 'pass')
        self.assertEqual(release.state, 'done')
        self.assertEqual(release.company_id, production.company_id)
        self.assertEqual(release.move_line_ids.lot_id, lot)
        self.assertEqual(release.location_dest_id, stock)
        self.assertEqual(release.quantity, 5)
        self.assertFalse(quant._get_available_quantity(product, quarantine, lot_id=lot))
        if 'stock_valuation_layer_ids' in release._fields:
            self.assertFalse(release.stock_valuation_layer_ids)
        check.do_pass()
        self.assertEqual(receipt.quality_release_move_ids, release)

    def test_pass_before_posting_releases_once_even_when_mo_close_is_retried(self):
        production, check, quarantine, stock = self._create_quarantine_order()
        production.workorder_ids.button_start(bypass=True)
        production.set_qty_producing()
        self._pass_quarantine_check(check)
        self.assertNotEqual(production.state, 'done')
        production.button_mark_done()

        receipt = production.move_finished_ids
        release = receipt.quality_release_move_ids
        self.assertEqual(production.state, 'done')
        self.assertEqual(receipt.location_dest_id, quarantine)
        self.assertEqual(release.location_dest_id, stock)
        self.assertEqual(release.state, 'done')
        self.assertEqual(release.move_line_ids.lot_id, production.lot_producing_id)
        self.assertEqual(release.quantity, 5)

        consumption_links = receipt.move_line_ids.consume_line_ids
        self.assertTrue(production.button_mark_done())
        self.assertEqual(receipt.quality_release_move_ids, release)
        self.assertEqual(receipt.move_line_ids.consume_line_ids, consumption_links)

    def test_quarantine_fail_and_missing_required_check_hold_output(self):
        production, check, quarantine, _stock = self._create_quarantine_order()
        production.button_mark_done()
        check.do_fail()
        self.assertEqual(check.quality_state, 'fail')
        self.assertEqual(production.quality_quarantine_status, 'fail')
        self.assertEqual(production.move_finished_ids.location_dest_id, quarantine)
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'draft')
        alert = self.env['quality.alert'].create({'check_id': check.id, 'team_id': check.team_id.id})
        self.assertEqual(alert.check_id, check)

        # Keep the mandatory point but remove the QC before any product is posted.
        production, _check, quarantine, _stock = self._create_quarantine_order(with_check=False)
        production.button_mark_done()
        self.assertEqual(production.quality_quarantine_status, 'pending')
        self.assertEqual(production.move_finished_ids.location_dest_id, quarantine)

    def test_three_passed_samples_cannot_release_five_produced_pieces(self):
        production, check, quarantine, _stock = self._create_quarantine_order()
        production.workorder_ids.state = 'progress'
        production.qty_producing = 3
        self._pass_quarantine_check(check)
        self.assertEqual(len(check.sample_ids), 3)
        production.qty_producing = 5
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        self.assertEqual(check.quality_state, 'pass')
        self.assertEqual(production.quality_quarantine_status, 'pending')
        self.assertEqual(production.move_finished_ids.location_dest_id, quarantine)
        self.assertEqual(production.move_finished_ids.quantity, 5)
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'draft')

    def test_rework_preserves_numeric_failure_and_logs_only_actual_corrections(self):
        self.quality_point.x_control_stage = 'in_process'
        check = self._create_check()
        check._create_measurement_samples(1, 1, AUTOMATIC_SAMPLE_INITIALIZATION)
        sample = check.sample_ids
        check.update_measurement_visual_result(sample.id, 'yes')
        numeric = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'numeric')
        boolean = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'boolean')
        text = sample.measurement_value_ids.filtered(lambda value: value.parameter_type == 'string')
        check.update_measurement_value(boolean.id, {'boolean_value': 'yes'})
        check.update_measurement_value(text.id, {'string_value': 'OK', 'manual_result': 'pass'})
        check.update_measurement_value(numeric.id, {'numeric_input': '11'})
        self.assertEqual(sample.sample_result, 'fail')
        check.update_measurement_value(numeric.id, {'numeric_input': '7'})
        self.assertEqual(sample.sample_result, 'pass')
        self.assertTrue(sample.x_was_reworked)
        self.assertEqual(sample.initial_failures[str(numeric.column_id.id)]['value'], 11)
        audit = check.message_ids.filtered(lambda message: 'FAIL на PASS' in message.body)
        self.assertEqual(len(audit), 1)
        self.assertIn('11', audit.body)
        self.assertIn('7', audit.body)
        check.update_measurement_value(numeric.id, {'numeric_input': '7'})
        self.assertEqual(check.message_ids.filtered(lambda message: 'FAIL на PASS' in message.body), audit)
        check.do_fail()
        alert = self.env['quality.alert'].create({'check_id': check.id, 'team_id': check.team_id.id})
        self.assertIn('початкова невідповідність', alert.quality_failure_snapshot)
        self.assertIn('11', alert.description)
        self.assertNotIn(boolean.column_id.parameter_name, alert.description)

    def _fail_held_production(self):
        production, check, quarantine, stock = self._create_quarantine_order()
        production.button_mark_done()
        samples = check.get_measurement_matrix_data()['samples']
        check.update_measurement_visual_result(samples[0]['id'], 'no')
        check.do_fail()
        return production, check, quarantine, stock

    def test_linked_recheck_releases_only_its_original_batch(self):
        if not self.env.user.has_group('quality.group_quality_user'):
            self.env.user.groups_id = [Command.link(self.env.ref('quality.group_quality_user').id)]
        production, check, quarantine, stock = self._fail_held_production()
        original_lot = check.automatic_sample_lot_id
        self.assertEqual(quarantine, production.move_finished_ids.location_dest_id)
        with self.assertRaises(UserError):
            check.update_measurement_visual_result(check.sample_ids[0].id, 'yes')
        with self.assertRaises(UserError):
            check.write({'quality_state': 'none'})
        with self.assertRaises(AccessError):
            self.env['quality.check'].with_context(quality_vataga_recheck=True).create({
                'quality_recheck_of_id': check.id, 'point_id': check.point_id.id,
            })
        check.point_id.x_control_stage = 'incoming'
        action = check.action_open_quality_recheck()
        self.assertEqual(action, check.action_open_quality_recheck())
        retry = self.env['quality.check'].browse(action['res_id'])
        self.assertEqual(retry.quality_recheck_of_id, check)
        self.assertEqual(retry.production_id, production)
        self.assertEqual(retry.automatic_sample_lot_id, original_lot)
        self.assertEqual(len(retry.sample_ids), 5)
        with self.assertRaises(UserError):
            retry.write({'lot_id': False})
        self._pass_quarantine_check(retry)
        self.assertEqual(check.quality_state, 'fail')
        self.assertEqual(check.sample_ids[0].visual_result, 'no')
        self.assertEqual(production.quality_quarantine_status, 'pass')
        release = production.move_finished_ids.quality_release_move_ids
        self.assertEqual(release.location_dest_id, stock)
        self.assertEqual(release.state, 'done')

    def _board_alert_for_held_batch(self, check, decision):
        for group in ('quality_vataga.group_quality_board_approval', 'stock.group_stock_user', 'mrp.group_mrp_user'):
            if not self.env.user.has_group(group):
                self.env.user.groups_id = [Command.link(self.env.ref(group).id)]
        return self.env['quality.alert'].create({
            'check_id': check.id, 'team_id': check.team_id.id,
            'product_id': check.production_id.product_id.id, 'company_id': check.company_id.id,
            'decision_type': decision, 'approval_user_id': self.env.uid,
            'decision_reason': 'Рішення за результатами випробування партії',
        })

    def test_board_acceptance_is_approved_exactly_once_and_does_not_rewrite_qc(self):
        production, check, quarantine, stock = self._fail_held_production()
        alert = self._board_alert_for_held_batch(check, 'accept_as_is')
        with self.assertRaises(AccessError):
            alert.action_execute_board_quarantine()
        alert.action_send_for_board_approval()
        with self.assertRaises(UserError):
            alert.write({'decision_type': 'scrap'})
        alert.action_approve_technical_board()
        self.assertEqual(alert.board_quantity, 5)
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'draft')
        alert.action_execute_board_quarantine()
        execution_date = alert.board_executed_at
        alert.action_execute_board_quarantine()
        release = production.move_finished_ids.quality_release_move_ids
        self.assertEqual(alert.board_executed_at, execution_date)
        self.assertEqual(release.location_dest_id, stock)
        self.assertEqual(release.location_id, quarantine)
        self.assertEqual(release.quantity, 5)
        self.assertEqual(alert.board_transfer_id, release.picking_id)
        self.assertEqual(production.quality_quarantine_status, 'accepted')
        self.assertEqual(check.quality_state, 'fail')

    def test_board_scrap_uses_standard_operation_and_keeps_produced_quantity(self):
        production, check, quarantine, stock = self._fail_held_production()
        alert = self._board_alert_for_held_batch(check, 'scrap')
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        alert.action_execute_board_quarantine()
        scrap = alert.board_scrap_id
        alert.action_execute_board_quarantine()
        self.assertEqual(alert.board_scrap_id, scrap)
        self.assertEqual(scrap.state, 'done')
        self.assertEqual(scrap.location_id, quarantine)
        self.assertEqual(scrap.lot_id, production.lot_producing_id)
        self.assertEqual(scrap.production_id, production)
        self.assertEqual(scrap.scrap_qty, 5)
        self.assertEqual(production.qty_produced, 5)
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'cancel')
        self.assertFalse(self.env['stock.quant']._get_available_quantity(production.product_id, stock))

    def test_board_full_control_cannot_exceed_batch_and_supplier_return_stays_held(self):
        production, check, _quarantine, _stock = self._fail_held_production()
        alert = self._board_alert_for_held_batch(check, 'full_control')
        lines = self.env['quality.alert.full.control.line']
        lines.create({'alert_id': alert.id, 'checked_qty': 3, 'good_qty': 2, 'bad_qty': 1})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            lines.create({'alert_id': alert.id, 'checked_qty': 3, 'good_qty': 3, 'bad_qty': 0})
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        alert = self._board_alert_for_held_batch(check, 'return_supplier')
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        with self.assertRaisesRegex(UserError, 'не має підтвердженого надходження'):
            alert.action_execute_board_quarantine()
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'draft')

    def test_board_unbuild_requires_original_consumed_components(self):
        production, check, _quarantine, _stock = self._fail_held_production()
        alert = self._board_alert_for_held_batch(check, 'unbuild')
        alert.action_send_for_board_approval()
        alert.action_approve_technical_board()
        with self.assertRaisesRegex(UserError, 'проведені компоненти'):
            alert.action_execute_board_quarantine()
        self.assertFalse(alert.board_unbuild_id)
        self.assertFalse(alert.board_executed_at)
        self.assertEqual(production.move_finished_ids.quality_release_move_ids.state, 'draft')

    def test_uncontrolled_production_keeps_its_original_destination(self):
        production, _check, _quarantine, stock = self._create_quarantine_order(controlled=False)
        production.button_mark_done()
        self.assertEqual(production.state, 'done')
        self.assertFalse(production.quality_quarantine_status)
        self.assertEqual(production.move_finished_ids.location_dest_id, stock)
        self.assertFalse(production.move_finished_ids.quality_release_move_ids)

    def test_quarantine_cannot_defer_required_production_registration(self):
        production, check, _quarantine, _stock = self._create_quarantine_order()
        registration_type = self.env['quality.point.test_type'].search([
            ('technical_name', '=', 'register_production'),
        ], limit=1)
        registration = self.env['quality.check'].create({
            'production_id': production.id, 'workorder_id': check.workorder_id.id,
            'test_type_id': registration_type.id, 'team_id': check.team_id.id,
            'product_id': production.product_id.id,
        })
        self.assertTrue(check.quality_quarantine_deferred)
        self.assertFalse(registration.quality_quarantine_deferred)
        with self.assertRaises(UserError), self.cr.savepoint():
            check.workorder_id.pre_record_production()
        with self.assertRaises(UserError), self.cr.savepoint():
            production.button_mark_done()
        self.assertEqual(registration.quality_state, 'none')
        self.assertFalse(production.move_finished_ids.quality_release_move_ids)

    def test_visual_only_no_result_blocks_pass_but_allows_fail(self):
        point = self._create_visual_only_point(
            'Невдалий візуальний контроль',
        )
        check = self._create_check_for_point(point)
        sample = self._add_sample(check)

        check.update_measurement_visual_result(sample.id, 'no')

        self.assertEqual(sample.sample_result, 'fail')
        self.assertTrue(sample.is_complete)
        self.assertTrue(sample.has_failure)
        self.assertTrue(check.measurement_matrix_has_failure)
        self.assertFalse(check.can_pass_measurement_check)
        with self.assertRaisesRegex(
            ValidationError,
            'Доступний лише результат',
        ):
            check.do_pass()
        check.do_fail()
        self.assertEqual(check.quality_state, 'fail')

    def test_all_visual_only_samples_must_pass(self):
        point = self._create_visual_only_point(
            'Груповий візуальний контроль',
        )
        check = self._create_check_for_point(point)
        check.add_measurement_samples(3)
        first_sample = check.sample_ids.filtered(
            lambda sample: sample.sample_number == 1,
        )
        check.update_measurement_visual_result(first_sample.id, 'yes')

        self.assertFalse(check.measurement_matrix_complete)
        self.assertFalse(check.can_pass_measurement_check)
        for sample in check.sample_ids - first_sample:
            check.update_measurement_visual_result(sample.id, 'yes')

        self.assertTrue(check.measurement_matrix_complete)
        self.assertTrue(check.can_pass_measurement_check)

    def test_visual_only_empty_tail_can_be_removed(self):
        point = self._create_visual_only_point(
            'Видалення порожніх візуальних зразків',
        )
        check = self._create_check_for_point(point)
        check.add_measurement_samples(3)

        matrix_data = check.remove_measurement_samples(2)

        self.assertEqual(check.sample_ids.mapped('sample_number'), [1])
        self.assertEqual(len(matrix_data['samples']), 1)
        self.assertFalse(check.sample_ids.measurement_value_ids)

    def test_visual_only_snapshot_is_immutable_after_completion(self):
        point = self._create_visual_only_point(
            'Історичний візуальний контроль',
        )
        check = self._create_check_for_point(point)
        check.do_fail()

        point.visual_sample_control_required = False
        check.invalidate_recordset(['measurement_matrix_required'])

        self.assertEqual(check.quality_state, 'fail')
        self.assertTrue(check.measurement_matrix_required)
        self.assertFalse(check.measurement_column_ids)
        self.assertFalse(check.sample_ids)

    def test_existing_unfinished_visual_only_check_can_be_initialized(self):
        point = self._create_visual_only_point(
            'Legacy візуальний контроль',
            required=False,
        )
        check = self._create_check_for_point(point)
        point.visual_sample_control_required = True

        self.assertTrue(check.can_initialize_measurement_matrix)
        check.action_initialize_measurement_matrix()

        self.assertTrue(check.measurement_matrix_required)
        self.assertFalse(check.measurement_column_ids)
        self.assertFalse(check.equipment_selection_ids)
        self.assertFalse(check.sample_ids)

    def test_visual_only_post_migration_is_idempotent(self):
        point = self._create_visual_only_point(
            'Міграція візуального контролю',
            required=False,
        )
        check = self._create_check_for_point(point)
        point.visual_sample_control_required = True

        self._run_post_migration('17.0.2.17', '17.0.2.16')
        self._run_post_migration('17.0.2.17', '17.0.2.16')

        self.assertTrue(check.measurement_matrix_required)
        self.assertFalse(check.measurement_column_ids)
        self.assertFalse(check.equipment_selection_ids)
        self.assertFalse(check.sample_ids)

    def test_post_migration_marks_existing_structured_matrix_required(self):
        check = self._create_check()
        column_ids = check.measurement_column_ids.ids
        selection_ids = check.equipment_selection_ids.ids
        self.cr.execute(
            """
            UPDATE quality_check
               SET measurement_matrix_required = FALSE
             WHERE id = %s
            """,
            [check.id],
        )
        check.invalidate_recordset(['measurement_matrix_required'])

        self._run_post_migration('17.0.2.17', '17.0.2.16')

        self.assertTrue(check.measurement_matrix_required)
        self.assertEqual(check.measurement_column_ids.ids, column_ids)
        self.assertEqual(check.equipment_selection_ids.ids, selection_ids)

    def test_check_creation_takes_immutable_snapshot(self):
        check = self._create_check()

        self.assertTrue(check.measurement_matrix_required)
        self.assertEqual(len(check.measurement_column_ids), 3)
        self.assertEqual(len(check.equipment_selection_ids), 1)
        self.assertEqual(
            check.equipment_selection_ids.allowed_equipment_category_ids,
            self.category | self.alternative_category,
        )
        self.assertFalse(check.sample_ids)
        numeric_column = check.measurement_column_ids.filtered(
            lambda column: column.parameter_type == 'numeric',
        )
        expected_category_ids = sorted([
            self.category.id,
            self.alternative_category.id,
        ])
        expected_category_key = ','.join(
            str(category_id)
            for category_id in expected_category_ids
        )
        expected_category_names = (
            'Мультиметри (тест), '
            'Струмовимірювальні кліщі (тест)'
        )
        for column in check.measurement_column_ids:
            self.assertEqual(
                column.equipment_category_ids,
                self.category | self.alternative_category,
            )
            self.assertEqual(
                column.category_set_key,
                expected_category_key,
            )
            self.assertEqual(
                column.equipment_category_names_snapshot,
                expected_category_names,
            )
        self.assertTrue(numeric_column.has_min_tolerance)
        self.assertEqual(numeric_column.min_tolerance, 0.0)
        self.assertEqual(numeric_column.max_tolerance, 10.0)

        self.numeric_line.write({'max_tolerance_input': '5'})
        self.numeric_line.write({
            'equipment_category_ids': [
                Command.set(self.category.ids),
            ],
        })
        self.numeric_parameter.write({
            'name': 'Перейменована напруга',
            'unit': 'мВ',
        })
        self.category.name = 'Перейменовані мультиметри'
        self.alternative_category.name = 'Перейменовані кліщі'

        self.assertEqual(numeric_column.parameter_name, 'Напруга')
        self.assertEqual(numeric_column.parameter_unit, 'В')
        self.assertEqual(numeric_column.max_tolerance, 10.0)
        self.assertEqual(
            sorted(numeric_column.equipment_category_ids.ids),
            expected_category_ids,
        )
        self.assertEqual(
            numeric_column.category_set_key,
            expected_category_key,
        )
        self.assertEqual(
            numeric_column.equipment_category_names_snapshot,
            expected_category_names,
        )

    def test_no_equipment_category_keeps_column_without_selection(self):
        point, line = self._create_boolean_point(
            'Самостійна перевірка без ЗВТ',
            self.no_equipment_category,
        )

        check = self._create_check_for_point(point)

        self.assertEqual(len(check.measurement_column_ids), 1)
        self.assertEqual(check.measurement_column_ids.source_line_id, line)
        self.assertFalse(check.equipment_selection_ids)
        self.assertFalse(self.env[
            'quality.check.equipment.selection'
        ].search([('quality_check_id', '=', check.id)]))

    def test_no_equipment_check_passes_without_selection(self):
        point, _line = self._create_boolean_point(
            'Успішна самостійна перевірка',
            self.no_equipment_category,
        )
        check = self._create_check_for_point(point)
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'yes')
        check.update_measurement_value(
            sample.measurement_value_ids.id,
            {'boolean_value': 'yes'},
        )

        self.assertTrue(check.equipment_selection_complete)
        self.assertTrue(check.can_pass_measurement_check)
        check.do_pass()
        self.assertEqual(check.quality_state, 'pass')

    def test_existing_no_equipment_selection_is_ignored_while_open(self):
        self.no_equipment_category.requires_equipment_selection = True
        point, _line = self._create_boolean_point(
            'Legacy selection без обладнання',
            self.no_equipment_category,
        )
        check = self._create_check_for_point(point)
        technical_selection = self.env[
            'quality.check.equipment.selection'
        ].search([('quality_check_id', '=', check.id)])
        self.assertTrue(technical_selection)

        self.no_equipment_category.requires_equipment_selection = False
        technical_selection.invalidate_recordset()
        check.invalidate_recordset([
            'equipment_selection_ids',
            'equipment_selection_complete',
            'can_pass_measurement_check',
        ])

        self.assertFalse(technical_selection.requires_equipment_selection)
        self.assertFalse(check.equipment_selection_ids)
        self.assertTrue(check.equipment_selection_complete)

    def test_mixed_categories_require_only_regular_equipment(self):
        point, _line = self._create_boolean_point(
            'Змішаний набір категорій',
            self.category | self.no_equipment_category,
        )

        check = self._create_check_for_point(point)
        selection = check.equipment_selection_ids
        column = check.measurement_column_ids

        self.assertEqual(
            column.equipment_category_ids,
            self.category | self.no_equipment_category,
        )
        self.assertEqual(
            selection.allowed_equipment_category_ids,
            self.category,
        )
        self.assertEqual(
            selection.required_equipment_category_ids,
            self.category,
        )
        with self.cr.savepoint(), self.assertRaises(ValidationError):
            selection.write({
                'equipment_ids': [Command.set(
                    self.no_equipment_category_equipment.ids,
                )],
            })
        selection.invalidate_recordset(['equipment_ids', 'equipment_id'])

        selection.equipment_ids = [Command.set(self.equipment.ids)]
        self.assertTrue(check.equipment_selection_complete)

    def test_completed_equipment_snapshot_is_not_changed_by_category_flag(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection.equipment_ids = [Command.set(self.equipment.ids)]
        check.do_fail()
        expected_snapshot = selection.equipment_display_list_snapshot
        expected_equipment = selection.equipment_ids
        self._run_post_migration('17.0.2.15', '17.0.2.14')

        (self.category | self.alternative_category).write({
            'requires_equipment_selection': False,
        })
        selection.invalidate_recordset()
        check.invalidate_recordset(['equipment_selection_ids'])

        self.assertTrue(selection.requires_equipment_selection)
        self.assertEqual(check.equipment_selection_ids, selection)
        self.assertEqual(selection.equipment_ids, expected_equipment)
        self.assertEqual(
            selection.equipment_display_list_snapshot,
            expected_snapshot,
        )

    def test_regular_category_still_requires_equipment(self):
        point, _line = self._create_boolean_point(
            'Звичайна категорія потребує ЗВТ',
            self.category,
        )

        check = self._create_check_for_point(point)

        self.assertEqual(len(check.equipment_selection_ids), 1)
        self.assertFalse(check.equipment_selection_complete)
        with self.assertRaisesRegex(
            ValidationError,
            'Оберіть щонайменше один допустимий прилад',
        ):
            check._validate_measurement_can_pass()

    def test_migration_marks_normalized_target_category_idempotently(self):
        target_category = self.env[
            'maintenance.equipment.category'
        ].create({
            'name': '  Тестування   справності  ',
        })
        untouched_category = self.env[
            'maintenance.equipment.category'
        ].create({
            'name': 'Тестування справності інше',
        })

        self._run_post_migration('17.0.2.15', '17.0.2.14')
        self._run_post_migration('17.0.2.15', '17.0.2.14')

        self.assertFalse(target_category.requires_equipment_selection)
        self.assertTrue(untouched_category.requires_equipment_selection)

    def test_add_samples_creates_one_cell_per_column(self):
        check = self._create_check()
        matrix_data = check.add_measurement_samples(2)

        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2])
        self.assertEqual(
            len(check.sample_ids.measurement_value_ids),
            6,
        )
        self.assertEqual(len(matrix_data['samples']), 2)

    def test_add_samples_rejects_invalid_count(self):
        check = self._create_check()

        for invalid_count in (1.5, 'abc', 0, -1, True):
            with self.subTest(count=invalid_count), self.assertRaises(
                ValidationError,
            ):
                check.add_measurement_samples(invalid_count)

        self.assertFalse(check.sample_ids)

    def test_remove_samples_deletes_empty_tail_and_reuses_numbers(self):
        check = self._create_check()
        check.add_measurement_samples(3)
        samples_to_remove = check.sample_ids.filtered(
            lambda sample: sample.sample_number in (2, 3),
        )
        removed_sample_ids = samples_to_remove.ids
        removed_value_ids = samples_to_remove.measurement_value_ids.ids

        matrix_data = check.remove_measurement_samples(2)

        remaining_samples = self.env['quality.check.sample'].search([
            ('quality_check_id', '=', check.id),
        ])
        self.assertEqual(remaining_samples.mapped('sample_number'), [1])
        self.assertFalse(
            self.env['quality.check.sample'].browse(
                removed_sample_ids,
            ).exists(),
        )
        self.assertFalse(
            self.env['quality.check.measurement.value'].browse(
                removed_value_ids,
            ).exists(),
        )
        self.assertEqual(
            [sample['sample_number'] for sample in matrix_data['samples']],
            [1],
        )

        check.add_measurement_samples(2)
        self.assertEqual(
            self.env['quality.check.sample'].search([
                ('quality_check_id', '=', check.id),
            ]).mapped('sample_number'),
            [1, 2, 3],
        )

    def test_remove_samples_rejects_invalid_or_excessive_count(self):
        check = self._create_check()
        check.add_measurement_samples(2)

        for invalid_count in (1.5, '1', 0, -1, True):
            with self.subTest(count=invalid_count), self.assertRaises(
                ValidationError,
            ):
                check.remove_measurement_samples(invalid_count)

        with self.assertRaisesRegex(
            ValidationError,
            'Неможливо прибрати 3 зразків',
        ):
            check.remove_measurement_samples(3)
        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2])

    def test_remove_samples_is_atomic_when_tail_contains_results(self):
        check = self._create_check()
        check.add_measurement_samples(3)
        second_sample = check.sample_ids.filtered(
            lambda sample: sample.sample_number == 2,
        )
        check.update_measurement_visual_result(second_sample.id, 'yes')

        with self.assertRaisesRegex(
            UserError,
            'зразок №2 вже містить введені результати',
        ):
            check.remove_measurement_samples(2)

        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2, 3])

    def test_remove_samples_is_blocked_after_check_completion(self):
        check = self._create_check()
        check.add_measurement_samples(1)
        self.cr.execute(
            """
            UPDATE quality_check
               SET quality_state = 'fail'
             WHERE id = %s
            """,
            [check.id],
        )
        check.invalidate_recordset(['quality_state'])

        with self.assertRaisesRegex(
            UserError,
            'Не можна видаляти зразки завершеної перевірки',
        ):
            check.remove_measurement_samples(1)
        self.assertEqual(check.sample_ids.mapped('sample_number'), [1])

    def test_complete_passing_matrix_allows_pass(self):
        check = self._create_check()
        check.equipment_selection_ids.equipment_ids = [
            Command.set(self.equipment.ids),
        ]
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'yes')

        numeric_value = sample.measurement_value_ids.filtered(
            lambda value: value.parameter_type == 'numeric',
        )
        boolean_value = sample.measurement_value_ids.filtered(
            lambda value: value.parameter_type == 'boolean',
        )
        string_value = sample.measurement_value_ids.filtered(
            lambda value: value.parameter_type == 'string',
        )
        check.update_measurement_value(
            numeric_value.id,
            {'numeric_input': '0'},
        )
        check.update_measurement_value(
            boolean_value.id,
            {'boolean_value': 'yes'},
        )
        check.update_measurement_value(
            string_value.id,
            {
                'string_value': 'Світиться',
                'manual_result': 'pass',
            },
        )

        self.assertEqual(sample.sample_result, 'pass')
        self.assertTrue(check.measurement_matrix_complete)
        self.assertFalse(check.measurement_matrix_has_failure)
        self.assertTrue(check.equipment_selection_complete)
        self.assertTrue(check.can_pass_measurement_check)
        check._validate_measurement_can_pass()

    def test_failure_blocks_pass_but_keeps_failure_available(self):
        check = self._create_check()
        check.equipment_selection_ids.equipment_ids = [
            Command.set(self.equipment.ids),
        ]
        sample = self._add_sample(check)
        check.update_measurement_visual_result(sample.id, 'yes')
        numeric_value = sample.measurement_value_ids.filtered(
            lambda value: value.parameter_type == 'numeric',
        )
        check.update_measurement_value(
            numeric_value.id,
            {'numeric_input': '11'},
        )

        self.assertEqual(numeric_value.result, 'fail')
        self.assertEqual(sample.sample_result, 'fail')
        self.assertTrue(check.measurement_matrix_has_failure)
        with self.assertRaisesRegex(
            ValidationError,
            'Доступний лише результат',
        ):
            check._validate_measurement_can_pass()

    def test_missing_equipment_has_specific_pass_error(self):
        check = self._create_check()
        self._add_sample(check)

        with self.assertRaisesRegex(
            ValidationError,
            'Оберіть щонайменше один допустимий прилад',
        ):
            check._validate_measurement_can_pass()

    def test_equipment_must_match_snapshot_category(self):
        check = self._create_check()
        with self.assertRaises(ValidationError):
            check.equipment_selection_ids.write({
                'equipment_ids': [
                    Command.set(self.other_equipment.ids),
                ],
            })

    def test_selected_equipment_is_snapshotted_for_history(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection.equipment_ids = [Command.set(self.equipment.ids)]

        check._snapshot_selected_equipment()

        self.assertEqual(
            selection.equipment_name_snapshot,
            'Цифровий мультиметр',
        )
        self.assertEqual(
            selection.equipment_number_snapshot,
            'DMM-0002',
        )
        self.assertEqual(
            selection.equipment_number_label_snapshot,
            'Серійний номер',
        )
        self.assertEqual(
            selection.equipment_display_snapshot,
            '[DMM-0002] Цифровий мультиметр',
        )
        self.assertEqual(
            selection.equipment_display_list_snapshot,
            '[DMM-0002] Цифровий мультиметр',
        )

    def test_multiple_equipment_from_one_allowed_category_is_complete(self):
        check = self._create_check()
        selection = check.equipment_selection_ids

        selection.equipment_ids = [Command.set([
            self.equipment.id,
            self.second_equipment.id,
        ])]

        self.assertTrue(check.equipment_selection_complete)
        self.assertEqual(selection.equipment_id, self.equipment)

    def test_equipment_multiselect_onchange_handles_virtual_records(self):
        equipment_ids = [
            self.second_equipment.id,
            self.equipment.id,
        ]
        selection = self.env[
            'quality.check.equipment.selection'
        ].new({
            'equipment_ids': [
                Command.set(equipment_ids),
            ],
        })

        selection._onchange_equipment_ids()

        self.assertEqual(len(selection.equipment_ids), 2)
        self.assertEqual(
            sorted(
                selection._get_persisted_record_id(equipment)
                for equipment in selection.equipment_ids
            ),
            sorted(equipment_ids),
        )
        self.assertEqual(
            selection._get_persisted_record_id(selection.equipment_id),
            min(equipment_ids),
        )

    def test_equipment_from_alternative_categories_is_complete(self):
        check = self._create_check()

        check.equipment_selection_ids.equipment_ids = [Command.set([
            self.equipment.id,
            self.alternative_equipment.id,
        ])]

        self.assertTrue(check.equipment_selection_complete)

    def test_same_category_set_creates_one_multiselect_row(self):
        point = self._create_quality_point(
            'Тестове групування наборів категорій',
        )
        line_model = self.env['quality.control.parameter.line']
        shared_categories = [
            self.category.id,
            self.alternative_category.id,
        ]
        lines = line_model.create([
            {
                'quality_point_id': point.id,
                'sequence': 10,
                'control_type': 'instrumental',
                'equipment_category_ids': [
                    Command.set(shared_categories),
                ],
                'parameter_id': self.numeric_parameter.id,
            },
            {
                'quality_point_id': point.id,
                'sequence': 20,
                'control_type': 'functional',
                'equipment_category_ids': [
                    Command.set(list(reversed(shared_categories))),
                ],
                'parameter_id': self.boolean_parameter.id,
                'text_norm': 'Так',
            },
            {
                'quality_point_id': point.id,
                'sequence': 30,
                'control_type': 'functional',
                'equipment_category_ids': [
                    Command.set(self.category.ids),
                ],
                'parameter_id': self.string_parameter.id,
                'text_norm': 'Працює',
            },
        ])
        check = self.env['quality.check'].create({
            'point_id': point.id,
            'team_id': point.team_id.id,
            'test_type_id': point.test_type_id.id,
        })

        self.assertEqual(len(check.measurement_column_ids), 3)
        self.assertEqual(len(check.equipment_selection_ids), 2)
        self.assertEqual(
            set(check.equipment_selection_ids.mapped('category_set_key')),
            {
                str(self.category.id),
                ','.join(str(category_id) for category_id in sorted(
                    shared_categories,
                )),
            },
        )
        shared_columns = check.measurement_column_ids.filtered(
            lambda column: column.source_line_id in lines[:2],
        )
        single_column = check.measurement_column_ids.filtered(
            lambda column: column.source_line_id == lines[2],
        )
        shared_key = ','.join(
            str(category_id)
            for category_id in sorted(shared_categories)
        )
        self.assertEqual(len(shared_columns), 2)
        self.assertEqual(len(single_column), 1)
        self.assertTrue(
            all(
                column.equipment_category_id == self.category
                for column in shared_columns | single_column
            ),
        )
        self.assertEqual(
            set(shared_columns.mapped('category_set_key')),
            {shared_key},
        )
        self.assertEqual(
            shared_columns[0].equipment_category_ids,
            self.category | self.alternative_category,
        )
        self.assertEqual(
            single_column.category_set_key,
            str(self.category.id),
        )
        self.assertEqual(
            single_column.equipment_category_ids,
            self.category,
        )

    def test_legacy_measurement_column_category_snapshot_is_backfilled(self):
        check = self._create_check()
        column = check.measurement_column_ids[0]
        legacy_category = column.equipment_category_id
        legacy_category_name = column.equipment_category_name
        self.cr.execute(
            """
            DELETE FROM quality_check_measurement_column_category_rel
             WHERE measurement_column_id = %s
            """,
            [column.id],
        )
        self.cr.execute(
            """
            UPDATE quality_check_measurement_column
               SET equipment_category_names_snapshot = NULL,
                   category_set_key = NULL
             WHERE id = %s
            """,
            [column.id],
        )
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self._run_post_migration('17.0.2.10', '17.0.2.9')
        self._run_post_migration('17.0.2.10', '17.0.2.9')
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self.assertEqual(
            column.equipment_category_ids,
            legacy_category,
        )
        self.assertEqual(
            column.equipment_category_names_snapshot,
            legacy_category_name,
        )
        self.assertEqual(
            column.category_set_key,
            str(legacy_category.id),
        )

    def test_full_column_category_snapshot_is_restored_from_selection(self):
        check = self._create_check()
        column = check.measurement_column_ids[0]
        selection = check.equipment_selection_ids
        expected_categories = selection.allowed_equipment_category_ids
        expected_key = selection.category_set_key
        expected_names = selection.equipment_category_names_snapshot
        legacy_category = column.equipment_category_id
        self.cr.execute(
            """
            DELETE FROM quality_check_measurement_column_category_rel
             WHERE measurement_column_id = %s
            """,
            [column.id],
        )
        self.cr.execute(
            """
            INSERT INTO quality_check_measurement_column_category_rel (
                measurement_column_id,
                equipment_category_id
            )
            VALUES (%s, %s)
            """,
            [column.id, legacy_category.id],
        )
        self.cr.execute(
            """
            UPDATE quality_check_measurement_column
               SET equipment_category_names_snapshot =
                       equipment_category_name,
                   category_set_key = equipment_category_id::text
             WHERE id = %s
            """,
            [column.id],
        )
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self._run_post_migration('17.0.2.11', '17.0.2.10')
        self._run_post_migration('17.0.2.11', '17.0.2.10')
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self.assertEqual(
            column.equipment_category_ids,
            expected_categories,
        )
        self.assertEqual(column.category_set_key, expected_key)
        self.assertEqual(
            column.equipment_category_names_snapshot,
            expected_names,
        )
        self.assertEqual(column.equipment_category_id, legacy_category)

    def test_column_category_fallback_is_untouched_without_selection_match(
        self,
    ):
        check = self._create_check()
        column = check.measurement_column_ids.filtered(
            lambda record: record.source_line_id == self.numeric_line,
        )
        legacy_category = column.equipment_category_id
        legacy_category_name = column.equipment_category_name
        self.numeric_line.write({
            'equipment_category_ids': [
                Command.set(self.category.ids),
            ],
        })
        self.cr.execute(
            """
            DELETE FROM quality_check_measurement_column_category_rel
             WHERE measurement_column_id = %s
            """,
            [column.id],
        )
        self.cr.execute(
            """
            INSERT INTO quality_check_measurement_column_category_rel (
                measurement_column_id,
                equipment_category_id
            )
            VALUES (%s, %s)
            """,
            [column.id, legacy_category.id],
        )
        self.cr.execute(
            """
            UPDATE quality_check_measurement_column
               SET equipment_category_names_snapshot =
                       equipment_category_name,
                   category_set_key = equipment_category_id::text
             WHERE id = %s
            """,
            [column.id],
        )
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self._run_post_migration('17.0.2.11', '17.0.2.10')
        column.invalidate_recordset([
            'equipment_category_ids',
            'equipment_category_names_snapshot',
            'category_set_key',
        ])

        self.assertEqual(
            column.equipment_category_ids,
            legacy_category,
        )
        self.assertEqual(
            column.equipment_category_names_snapshot,
            legacy_category_name,
        )
        self.assertEqual(
            column.category_set_key,
            str(legacy_category.id),
        )

    def test_category_set_key_is_unique_per_check(self):
        check = self._create_check()
        selection = check.equipment_selection_ids

        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env[
                'quality.check.equipment.selection'
            ].with_context(
                quality_vataga_snapshot_initialization=True,
            ).create({
                'quality_check_id': check.id,
                'sequence': 99,
                'equipment_category_id': self.category.id,
                'equipment_category_name': self.category.display_name,
                'allowed_equipment_category_ids': [
                    Command.set(
                        selection.allowed_equipment_category_ids.ids,
                    ),
                ],
                'equipment_category_names_snapshot':
                    selection.equipment_category_names_snapshot,
                'category_set_key': selection.category_set_key,
            })

    def test_public_write_cannot_change_selection_structure(self):
        selection = self._create_check().equipment_selection_ids

        for values in (
            {'equipment_id': self.equipment.id},
            {'category_set_key': str(self.other_category.id)},
            {
                'allowed_equipment_category_ids': [
                    Command.set(self.other_category.ids),
                ],
            },
        ):
            with self.subTest(values=values), self.assertRaises(UserError):
                selection.write(values)

    def test_completed_check_rejects_equipment_changes(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection.equipment_ids = [Command.set(self.equipment.ids)]
        check.do_fail()

        with self.assertRaises(UserError):
            selection.write({
                'equipment_ids': [
                    Command.set(self.second_equipment.ids),
                ],
            })

    def test_multiple_equipment_snapshot_is_immutable(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection.equipment_ids = [Command.set([
            self.equipment.id,
            self.alternative_equipment.id,
        ])]

        check.do_fail()
        expected_snapshot = (
            '[DMM-0002] Цифровий мультиметр\n'
            '[CLAMP-0002] Струмовимірювальні кліщі'
        )
        self.assertEqual(
            selection.equipment_display_list_snapshot,
            expected_snapshot,
        )

        self.equipment.name = 'Перейменований мультиметр'
        self.equipment.serial_no = 'DMM-CHANGED'
        self.equipment.category_id = self.other_category
        self.alternative_equipment.name = 'Перейменовані кліщі'
        self.alternative_equipment.serial_no = 'CLAMP-CHANGED'
        self.alternative_equipment.category_id = self.other_category

        self.assertEqual(
            selection.equipment_display_list_snapshot,
            expected_snapshot,
        )

    def test_legacy_equipment_is_migrated_to_multiselect(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection._write_equipment_snapshot({
            'equipment_name_snapshot': self.equipment.name,
            'equipment_number_snapshot': 'DMM-0002',
            'equipment_number_label_snapshot': 'Серійний номер',
        })
        self.cr.execute(
            """
            UPDATE quality_check_equipment_selection
               SET equipment_id = %s,
                   equipment_display_list_snapshot = NULL
             WHERE id = %s
            """,
            [self.equipment.id, selection.id],
        )
        self.cr.execute(
            """
            DELETE FROM quality_check_equipment_selection_equipment_rel
             WHERE selection_id = %s
            """,
            [selection.id],
        )
        self.cr.execute(
            """
            DELETE FROM quality_check_equipment_selection_category_rel
             WHERE selection_id = %s
            """,
            [selection.id],
        )
        self.cr.execute(
            """
            UPDATE quality_check
               SET quality_state = 'fail'
             WHERE id = %s
            """,
            [check.id],
        )
        selection.invalidate_recordset()
        check.invalidate_recordset()

        self.env[
            'quality.check.equipment.selection'
        ]._migrate_legacy_equipment_data()
        self.env[
            'quality.check.equipment.selection'
        ]._migrate_legacy_equipment_data()

        self.assertEqual(selection.equipment_ids, self.equipment)
        self.assertEqual(
            selection.allowed_equipment_category_ids,
            self.category,
        )
        self.assertEqual(
            selection.equipment_display_list_snapshot,
            '[DMM-0002] Цифровий мультиметр',
        )

    def test_serial_number_is_not_labeled_as_inventory_number(self):
        parameter = self.env['ir.config_parameter'].sudo()
        key = 'quality_vataga.equipment_inventory_field'
        original_value = parameter.get_param(key)
        try:
            parameter.set_param(key, 'serial_no')
            self.assertEqual(
                self.equipment._quality_vataga_get_equipment_number(),
                ('DMM-0002', 'Серійний номер'),
            )
        finally:
            parameter.set_param(key, original_value or False)

    def test_relation_cannot_be_used_as_equipment_number(self):
        parameter = self.env['ir.config_parameter'].sudo()
        key = 'quality_vataga.equipment_inventory_field'
        original_value = parameter.get_param(key)
        try:
            parameter.set_param(key, 'category_id')
            self.assertEqual(
                self.equipment._quality_vataga_get_equipment_number(),
                ('DMM-0002', 'Серійний номер'),
            )
        finally:
            parameter.set_param(key, original_value or False)

    def test_duplicate_samples_and_cells_are_rejected(self):
        check = self._create_check()
        sample = self._add_sample(check)
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env['quality.check.sample'].with_context(
                quality_vataga_sample_initialization=True,
            ).create({
                'quality_check_id': check.id,
                'sample_number': sample.sample_number,
            })

        value = sample.measurement_value_ids[0]
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env['quality.check.measurement.value'].with_context(
                quality_vataga_matrix_initialization=True,
            ).create({
                'quality_check_id': check.id,
                'sample_id': sample.id,
                'column_id': value.column_id.id,
            })
