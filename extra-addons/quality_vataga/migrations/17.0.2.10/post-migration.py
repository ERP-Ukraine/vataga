def _table_exists(cr, table_name):
    cr.execute('SELECT to_regclass(%s)', [table_name])
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    column_table = 'quality_check_measurement_column'
    relation_table = 'quality_check_measurement_column_category_rel'
    if not _table_exists(cr, column_table):
        return

    if _table_exists(cr, relation_table):
        cr.execute("""
            INSERT INTO quality_check_measurement_column_category_rel (
                measurement_column_id,
                equipment_category_id
            )
            SELECT column_record.id,
                   column_record.equipment_category_id
              FROM quality_check_measurement_column AS column_record
             WHERE column_record.equipment_category_id IS NOT NULL
               AND NOT EXISTS (
                    SELECT 1
                      FROM quality_check_measurement_column_category_rel
                           AS relation_record
                     WHERE relation_record.measurement_column_id =
                           column_record.id
                       AND relation_record.equipment_category_id =
                           column_record.equipment_category_id
               )
            ON CONFLICT DO NOTHING
        """)

    cr.execute("""
        UPDATE quality_check_measurement_column
           SET equipment_category_names_snapshot = equipment_category_name
         WHERE (
                   equipment_category_names_snapshot IS NULL
                   OR BTRIM(equipment_category_names_snapshot) = ''
               )
           AND equipment_category_name IS NOT NULL
    """)
    cr.execute("""
        UPDATE quality_check_measurement_column
           SET category_set_key = equipment_category_id::text
         WHERE (
                   category_set_key IS NULL
                   OR BTRIM(category_set_key) = ''
               )
           AND equipment_category_id IS NOT NULL
    """)
