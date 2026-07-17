from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestQualityControlParameterLine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.category = cls.env['maintenance.equipment.category'].create({
            'name': 'Тестова категорія ЗВТ',
        })
        cls.other_category = cls.env['maintenance.equipment.category'].create({
            'name': 'Інша тестова категорія ЗВТ',
        })
        cls.numeric_parameter = cls.env['quality.equipment.parameter'].create({
            'name': 'Тестовий числовий параметр',
            'parameter_type': 'numeric',
            'unit': 'мм',
        })
        cls.boolean_parameter = cls.env['quality.equipment.parameter'].create({
            'name': 'Тестовий булевий параметр',
            'parameter_type': 'boolean',
        })
        cls.category.applicable_parameter_ids = [
            Command.set([
                cls.numeric_parameter.id,
                cls.boolean_parameter.id,
            ]),
        ]

        quality_team = cls.env['quality.alert.team'].search([], limit=1)
        if not quality_team:
            quality_team = cls.env['quality.alert.team'].create({
                'name': 'Тестова команда якості',
            })
        test_type = cls.env['quality.point.test_type'].search([], limit=1)
        if not test_type:
            test_type = cls.env['quality.point.test_type'].create({
                'name': 'Тестовий тип',
                'technical_name': 'quality_vataga_test',
            })
        picking_type = cls.env['stock.picking.type'].search([], limit=1)
        cls.quality_point = cls.env['quality.point'].create({
            'title': 'Тестовий пункт контролю',
            'team_id': quality_team.id,
            'test_type_id': test_type.id,
            'picking_type_ids': [Command.set(picking_type.ids)],
        })

    def _line_values(self, **overrides):
        values = {
            'quality_point_id': self.quality_point.id,
            'control_type': 'instrumental',
            'equipment_category_id': self.category.id,
            'parameter_id': self.numeric_parameter.id,
        }
        values.update(overrides)
        return values

    def test_zero_tolerances_are_explicit_values(self):
        line = self.env['quality.control.parameter.line'].create(
            self._line_values(
                has_min_tolerance=True,
                min_tolerance=0.0,
                has_max_tolerance=True,
                max_tolerance=0.0,
            ),
        )

        self.assertTrue(line.has_min_tolerance)
        self.assertTrue(line.has_max_tolerance)
        self.assertEqual(line.min_tolerance, 0.0)
        self.assertEqual(line.max_tolerance, 0.0)

    def test_minimum_cannot_exceed_maximum(self):
        with self.assertRaises(ValidationError):
            self.env['quality.control.parameter.line'].create(
                self._line_values(
                    has_min_tolerance=True,
                    min_tolerance=2.0,
                    has_max_tolerance=True,
                    max_tolerance=1.0,
                ),
            )

    def test_single_sided_tolerances(self):
        minimum_line = self.env['quality.control.parameter.line'].create(
            self._line_values(
                has_min_tolerance=True,
                min_tolerance=1.0,
            ),
        )
        maximum_line = self.env['quality.control.parameter.line'].create(
            self._line_values(
                has_max_tolerance=True,
                max_tolerance=2.0,
            ),
        )

        self.assertTrue(minimum_line.has_min_tolerance)
        self.assertFalse(minimum_line.has_max_tolerance)
        self.assertFalse(maximum_line.has_min_tolerance)
        self.assertTrue(maximum_line.has_max_tolerance)

    def test_parameter_must_belong_to_category(self):
        with self.assertRaises(ValidationError):
            self.env['quality.control.parameter.line'].create(
                self._line_values(
                    equipment_category_id=self.other_category.id,
                ),
            )

    def test_category_onchange_clears_incompatible_parameter(self):
        line = self.env['quality.control.parameter.line'].new(
            self._line_values(),
        )

        line.equipment_category_id = self.other_category
        line._onchange_equipment_category_id()

        self.assertFalse(line.parameter_id)

    def test_category_onchange_keeps_compatible_parameter(self):
        line = self.env['quality.control.parameter.line'].new(
            self._line_values(),
        )

        line._onchange_equipment_category_id()

        self.assertEqual(line.parameter_id, self.numeric_parameter)

    def test_nonnumeric_parameter_rejects_tolerances(self):
        with self.assertRaises(ValidationError):
            self.env['quality.control.parameter.line'].create(
                self._line_values(
                    parameter_id=self.boolean_parameter.id,
                    has_min_tolerance=True,
                    min_tolerance=0.0,
                ),
            )

    def test_tolerance_value_requires_explicit_flag(self):
        with self.assertRaises(ValidationError):
            self.env['quality.control.parameter.line'].create(
                self._line_values(min_tolerance=1.0),
            )

    def test_parameter_onchange_clears_nonnumeric_tolerances(self):
        line = self.env['quality.control.parameter.line'].new(
            self._line_values(
                has_min_tolerance=True,
                min_tolerance=1.0,
                has_max_tolerance=True,
                max_tolerance=2.0,
            ),
        )

        line.parameter_id = self.boolean_parameter
        line._onchange_parameter_id()

        self.assertFalse(line.has_min_tolerance)
        self.assertFalse(line.has_max_tolerance)
        self.assertEqual(line.min_tolerance, 0.0)
        self.assertEqual(line.max_tolerance, 0.0)

    def test_quality_point_deletion_cascades_to_lines(self):
        point = self.quality_point.copy()
        line = self.env['quality.control.parameter.line'].create(
            self._line_values(quality_point_id=point.id),
        )

        point.unlink()

        self.assertFalse(line.exists())
