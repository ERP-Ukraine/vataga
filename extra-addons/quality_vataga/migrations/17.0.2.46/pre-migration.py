"""Retire UI metadata only; preserve every business table and historical row."""


def migrate(cr, version):
    # The old child XPath targets a widget removed from its parent view.
    # Disable it before parent validation; XML reload supplies the new XPath
    # and reactivates this same view (same external ID).
    cr.execute("""
        UPDATE ir_ui_view SET active = FALSE
        WHERE id IN (SELECT res_id FROM ir_model_data
            WHERE module = 'quality_vataga' AND model = 'ir.ui.view'
              AND name IN ('mrp_production_quality_unit_stock_release',
                'quality_unit_release_wizard_form',
                'quality_unit_stock_release_wizard_form',
                'stock_timer_binding_wizard_form'))
    """)
    # Keep retired form IDs for historical metadata / cached actions. They
    # are inactive, and all their server mutations are unconditionally denied.
    cr.execute("""
        UPDATE ir_model_data SET noupdate = TRUE
        WHERE module = 'quality_vataga' AND model = 'ir.ui.view'
          AND name IN ('quality_unit_release_wizard_form',
            'quality_unit_stock_release_wizard_form',
            'stock_timer_binding_wizard_form')
    """)
