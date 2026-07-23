from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityCheckMeasurementMatrix(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': 'Мультиметри (тест)',
        })
        cls.other_category = cls.env[
            'maintenance.equipment.category'
        ].create({
            'name': 'Інша категорія (тест)',
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

        cls.equipment = cls.env['maintenance.equipment'].create({
            'name': 'Цифровий мультиметр',
            'category_id': cls.category.id,
            'serial_no': 'DMM-0002',
        })
        cls.other_equipment = cls.env['maintenance.equipment'].create({
            'name': 'Інше обладнання',
            'category_id': cls.other_category.id,
            'serial_no': 'OTHER-1',
        })

        quality_team = cls.env['quality.alert.team'].search([], limit=1)
        if not quality_team:
            quality_team = cls.env['quality.alert.team'].create({
                'name': 'Тестова команда якості матриці',
            })
        test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not test_type:
            test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип матриці',
                'technical_name': 'quality_vataga_matrix_test',
            })
        picking_type = cls.env['stock.picking.type'].search([], limit=1)
        cls.quality_point = cls.env['quality.point'].create({
            'title': 'Тестовий QCP матриці',
            'team_id': quality_team.id,
            'test_type_id': test_type.id,
            'picking_type_ids': [Command.set(picking_type.ids)],
        })
        cls.numeric_line = cls.env[
            'quality.control.parameter.line'
        ].create({
            'quality_point_id': cls.quality_point.id,
            'sequence': 10,
            'control_type': 'instrumental',
            'equipment_category_id': cls.category.id,
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
            'equipment_category_id': cls.category.id,
            'parameter_id': cls.boolean_parameter.id,
            'text_norm': 'Так',
        })
        cls.string_line = cls.env[
            'quality.control.parameter.line'
        ].create({
            'quality_point_id': cls.quality_point.id,
            'sequence': 30,
            'control_type': 'functional',
            'equipment_category_id': cls.category.id,
            'parameter_id': cls.string_parameter.id,
            'text_norm': 'Працює коректно',
        })

    def _create_check(self):
        return self.env['quality.check'].create({
            'point_id': self.quality_point.id,
            'team_id': self.quality_point.team_id.id,
            'test_type_id': self.quality_point.test_type_id.id,
        })

    def _add_sample(self, check):
        check.sample_count_to_add = 1
        check.action_add_measurement_samples()
        return check.sample_ids

    def test_check_creation_takes_immutable_snapshot(self):
        check = self._create_check()

        self.assertEqual(len(check.measurement_column_ids), 3)
        self.assertEqual(len(check.equipment_selection_ids), 1)
        self.assertFalse(check.sample_ids)
        numeric_column = check.measurement_column_ids.filtered(
            lambda column: column.parameter_type == 'numeric',
        )
        self.assertTrue(numeric_column.has_min_tolerance)
        self.assertEqual(numeric_column.min_tolerance, 0.0)
        self.assertEqual(numeric_column.max_tolerance, 10.0)

        self.numeric_line.write({'max_tolerance_input': '5'})
        self.numeric_parameter.write({
            'name': 'Перейменована напруга',
            'unit': 'мВ',
        })

        self.assertEqual(numeric_column.parameter_name, 'Напруга')
        self.assertEqual(numeric_column.parameter_unit, 'В')
        self.assertEqual(numeric_column.max_tolerance, 10.0)

    def test_add_samples_creates_one_cell_per_column(self):
        check = self._create_check()
        check.sample_count_to_add = 2
        check.action_add_measurement_samples()

        self.assertEqual(check.sample_ids.mapped('sample_number'), [1, 2])
        self.assertEqual(
            len(check.sample_ids.measurement_value_ids),
            6,
        )

    def test_complete_passing_matrix_allows_pass(self):
        check = self._create_check()
        check.equipment_selection_ids.equipment_id = self.equipment
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
        check.equipment_selection_ids.equipment_id = self.equipment
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
            'Оберіть конкретний прилад',
        ):
            check._validate_measurement_can_pass()

    def test_equipment_must_match_snapshot_category(self):
        check = self._create_check()
        with self.assertRaises(ValidationError):
            check.equipment_selection_ids.write({
                'equipment_id': self.other_equipment.id,
            })

    def test_selected_equipment_is_snapshotted_for_history(self):
        check = self._create_check()
        selection = check.equipment_selection_ids
        selection.equipment_id = self.equipment

        check._snapshot_selected_equipment()

        self.assertEqual(
            selection.equipment_name_snapshot,
            'Цифровий мультиметр',
        )
        self.assertEqual(
            selection.equipment_inventory_snapshot,
            'DMM-0002',
        )

    def test_duplicate_samples_and_cells_are_rejected(self):
        check = self._create_check()
        sample = self._add_sample(check)
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env['quality.check.sample'].create({
                'quality_check_id': check.id,
                'sample_number': sample.sample_number,
            })

        value = sample.measurement_value_ids[0]
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env['quality.check.measurement.value'].create({
                'quality_check_id': check.id,
                'sample_id': sample.id,
                'column_id': value.column_id.id,
            })
