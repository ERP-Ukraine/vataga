def migrate(cr, version):
    cr.execute(
        """
        DELETE FROM ir_ui_view
         WHERE key = 'product_vataga.view_picking_form_box_count'
            OR id IN (
                SELECT res_id
                  FROM ir_model_data
                 WHERE module = 'product_vataga'
                   AND name = 'view_picking_form_box_count'
                   AND model = 'ir.ui.view'
            )
        """
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'product_vataga'
           AND name = 'view_picking_form_box_count'
        """
    )
