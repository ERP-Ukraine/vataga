import re

from odoo import api, SUPERUSER_ID


TARGET_CATEGORY_NAME = 'Тестування справності'


def _normalize_name(name):
    return re.sub(r'\s+', ' ', (name or '').strip())


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        UPDATE quality_check_equipment_selection AS selection
           SET preserve_completed_equipment_selection = TRUE
          FROM quality_check AS quality_check
         WHERE quality_check.id = selection.quality_check_id
           AND quality_check.quality_state != 'none'
           AND NOT selection.preserve_completed_equipment_selection
    """)
    env.invalidate_all()
    categories = env['maintenance.equipment.category'].with_context(
        active_test=False,
    ).search([])
    target_categories = categories.filtered(
        lambda category:
            _normalize_name(category.name) == TARGET_CATEGORY_NAME,
    )
    if target_categories:
        target_categories.write({
            'requires_equipment_selection': False,
        })
    env.flush_all()
