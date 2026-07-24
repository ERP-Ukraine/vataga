import os
import runpy

from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase, tagged


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
        return self.env['quality.check'].create({
            'point_id': self.quality_point.id,
            'team_id': self.quality_point.team_id.id,
            'test_type_id': self.quality_point.test_type_id.id,
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

    def test_check_creation_takes_immutable_snapshot(self):
        check = self._create_check()

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
