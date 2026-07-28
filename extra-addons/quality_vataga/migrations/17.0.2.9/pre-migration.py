from psycopg2 import sql


def migrate(cr, version):
    cr.execute("""
        SELECT constraint_record.conname
          FROM pg_constraint AS constraint_record
         WHERE constraint_record.conrelid = to_regclass(
                   'quality_check_equipment_selection'
               )
           AND constraint_record.contype = 'u'
           AND pg_get_constraintdef(constraint_record.oid)
               ~ 'UNIQUE \\(quality_check_id, equipment_category_id\\)'
    """)
    for (constraint_name,) in cr.fetchall():
        cr.execute(
            sql.SQL(
                'ALTER TABLE quality_check_equipment_selection '
                'DROP CONSTRAINT {}'
            ).format(sql.Identifier(constraint_name)),
        )
