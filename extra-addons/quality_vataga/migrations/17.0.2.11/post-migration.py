REQUIRED_TABLES = (
    'quality_check_measurement_column',
    'quality_check_measurement_column_category_rel',
    'quality_control_parameter_line_category_rel',
    'quality_check_equipment_selection',
    'quality_check_equipment_selection_category_rel',
)


def _table_exists(cr, table_name):
    cr.execute('SELECT to_regclass(%s)', [table_name])
    return bool(cr.fetchone()[0])


def migrate(cr, version):
    if not all(_table_exists(cr, table) for table in REQUIRED_TABLES):
        return

    cr.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS
            quality_vataga_measurement_column_category_matches (
            measurement_column_id BIGINT PRIMARY KEY,
            selection_id BIGINT NOT NULL,
            category_set_key TEXT NOT NULL,
            equipment_category_names_snapshot TEXT NOT NULL
        ) ON COMMIT DROP
    """)
    cr.execute("""
        TRUNCATE TABLE quality_vataga_measurement_column_category_matches
    """)
    cr.execute("""
        WITH source_category_sets AS (
            SELECT source_relation.line_id,
                   STRING_AGG(
                       source_relation.equipment_category_id::text,
                       ',' ORDER BY source_relation.equipment_category_id
                   ) AS category_set_key
              FROM (
                    SELECT DISTINCT line_id, equipment_category_id
                      FROM quality_control_parameter_line_category_rel
                     WHERE line_id IS NOT NULL
                       AND equipment_category_id IS NOT NULL
              ) AS source_relation
             GROUP BY source_relation.line_id
        ),
        selection_category_sets AS (
            SELECT selection_relation.selection_id,
                   STRING_AGG(
                       selection_relation.equipment_category_id::text,
                       ',' ORDER BY selection_relation.equipment_category_id
                   ) AS category_set_key
              FROM (
                    SELECT DISTINCT selection_id, equipment_category_id
                      FROM quality_check_equipment_selection_category_rel
                     WHERE selection_id IS NOT NULL
                       AND equipment_category_id IS NOT NULL
              ) AS selection_relation
             GROUP BY selection_relation.selection_id
        ),
        candidate_matches AS (
            SELECT column_record.id AS measurement_column_id,
                   selection_record.id AS selection_id,
                   selection_record.category_set_key,
                   selection_record.equipment_category_names_snapshot,
                   COUNT(*) OVER (
                       PARTITION BY column_record.id
                   ) AS match_count
              FROM quality_check_measurement_column AS column_record
              JOIN source_category_sets AS source_set
                ON source_set.line_id = column_record.source_line_id
              JOIN quality_check_equipment_selection AS selection_record
                ON selection_record.quality_check_id =
                   column_record.quality_check_id
               AND selection_record.category_set_key =
                   source_set.category_set_key
              JOIN selection_category_sets AS selection_set
                ON selection_set.selection_id = selection_record.id
               AND selection_set.category_set_key =
                   selection_record.category_set_key
             WHERE column_record.source_line_id IS NOT NULL
               AND COALESCE(
                       BTRIM(
                           selection_record.equipment_category_names_snapshot
                       ),
                       ''
                   ) <> ''
        )
        INSERT INTO quality_vataga_measurement_column_category_matches (
            measurement_column_id,
            selection_id,
            category_set_key,
            equipment_category_names_snapshot
        )
        SELECT measurement_column_id,
               selection_id,
               category_set_key,
               equipment_category_names_snapshot
          FROM candidate_matches
         WHERE match_count = 1
    """)

    cr.execute("""
        DELETE FROM quality_check_measurement_column_category_rel
              AS column_relation
         USING quality_vataga_measurement_column_category_matches
               AS matched_column
         WHERE column_relation.measurement_column_id =
               matched_column.measurement_column_id
    """)
    cr.execute("""
        INSERT INTO quality_check_measurement_column_category_rel (
            measurement_column_id,
            equipment_category_id
        )
        SELECT matched_column.measurement_column_id,
               selection_relation.equipment_category_id
          FROM quality_vataga_measurement_column_category_matches
               AS matched_column
          JOIN quality_check_equipment_selection_category_rel
               AS selection_relation
            ON selection_relation.selection_id =
               matched_column.selection_id
        ON CONFLICT DO NOTHING
    """)
    cr.execute("""
        UPDATE quality_check_measurement_column AS column_record
           SET category_set_key = matched_column.category_set_key,
               equipment_category_names_snapshot =
                   matched_column.equipment_category_names_snapshot
          FROM quality_vataga_measurement_column_category_matches
               AS matched_column
         WHERE column_record.id = matched_column.measurement_column_id
           AND (
                column_record.category_set_key IS DISTINCT FROM
                    matched_column.category_set_key
                OR column_record.equipment_category_names_snapshot
                    IS DISTINCT FROM
                    matched_column.equipment_category_names_snapshot
           )
    """)
