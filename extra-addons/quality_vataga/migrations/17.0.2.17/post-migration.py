from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    check_model = env['quality.check']

    structured_checks = check_model.search([
        ('measurement_matrix_required', '=', False),
        '|', '|',
        ('measurement_column_ids', '!=', False),
        ('equipment_selection_ids', '!=', False),
        ('sample_ids', '!=', False),
    ])
    structured_checks._set_measurement_matrix_required()

    visual_only_checks = check_model.search([
        ('measurement_matrix_required', '=', False),
        ('quality_state', '=', 'none'),
        ('point_id.visual_sample_control_required', '=', True),
        ('measurement_column_ids', '=', False),
        ('equipment_selection_ids', '=', False),
        ('sample_ids', '=', False),
    ]).filtered(
        lambda check: not check.point_id.control_parameter_line_ids,
    )
    visual_only_checks._initialize_measurement_snapshot()
    env.flush_all()
