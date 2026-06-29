def migrate(cr, version):
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'stock_picking'
           AND column_name = 'box_count'
        """
    )
    if not cr.fetchone():
        return

    cr.execute(
        """
        UPDATE stock_picking
           SET box_count = 1
         WHERE box_count IS NULL
            OR box_count < 1
        """
    )
