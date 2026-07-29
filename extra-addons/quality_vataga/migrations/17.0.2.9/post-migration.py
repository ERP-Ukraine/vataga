from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env[
        'quality.control.parameter.line'
    ]._migrate_legacy_equipment_categories()
    env[
        'quality.check.equipment.selection'
    ]._migrate_legacy_equipment_data()
    env.flush_all()

    cr.execute("""
        ALTER TABLE quality_check_equipment_selection
        ALTER COLUMN equipment_category_names_snapshot SET NOT NULL,
        ALTER COLUMN category_set_key SET NOT NULL
    """)
