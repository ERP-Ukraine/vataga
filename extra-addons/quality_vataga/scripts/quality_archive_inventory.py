"""Per-database, read-only evidence for FUTURE archive removal, never a migration.

Run with PostgreSQL read access and PGPASSWORD (or a configured password file):
python quality_archive_inventory.py --read-only-inventory --database DB --user USER

Print JSON to stdout. Run separately for every target database; staging counts
cannot authorize production cleanup. Installed addon code and report candidates
still require review. This script neither drops nor updates anything.
"""
import argparse
import json
import os

import psycopg2


ARCHIVE_MODELS = (
    'quality.unit.release.batch', 'quality.unit.release.line', 'quality.unit.release.event',
    'quality.unit.stock.release.plan', 'quality.stock.cost.scope', 'quality.stock.posting',
    'quality.stock.posting.line', 'quality.stock.cost.allocation', 'quality.stock.timer.binding',
)
TRANSIENT_MODELS = (
    'quality.unit.release.wizard', 'quality.unit.release.wizard.line',
    'quality.unit.stock.release.wizard', 'quality.unit.stock.release.wizard.line',
    'quality.stock.timer.binding.wizard',
)
LIVE_TABLES = {'mrp.production': 'mrp_production', 'quality.production.unit': 'quality_production_unit',
               'quality.check': 'quality_check', 'quality.alert': 'quality_alert'}


def identifier(value):
    return '"' + value.replace('"', '""') + '"'


def collect_inventory(cr, mo_name='CAB/MO/05095'):
    cr.execute('SHOW transaction_read_only')
    if cr.fetchone()[0] != 'on':
        raise RuntimeError('Inventory requires a read-only transaction.')
    cr.execute('SELECT current_database(), current_user')
    database, user = cr.fetchone()
    cr.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema=current_schema()")
    columns = {}
    for table, column in cr.fetchall():
        columns.setdefault(table, set()).add(column)
    unavailable = {}

    def query(sql, params=()):
        cr.execute(sql, params)
        names = [item[0] for item in cr.description]
        return [dict(zip(names, row)) for row in cr.fetchall()]

    def records(table, names, where='', params=()):
        if table not in columns:
            unavailable[table] = 'table absent'
            return []
        selected = [name for name in names.split() if name in columns[table]]
        missing = sorted(set(names.split()) - columns[table])
        if missing:
            unavailable[table] = missing  # includes nonstored ORM fields
        if not selected:
            return []
        return query('SELECT ' + ','.join(identifier(name) for name in selected) +
                     ' FROM ' + identifier(table) + (' WHERE ' + where if where else '') +
                     (' ORDER BY id' if 'id' in selected else ''), params)

    archive = []
    for model in ARCHIVE_MODELS + TRANSIENT_MODELS:
        table = model.replace('.', '_')
        count = query('SELECT COUNT(*) AS count FROM ' + identifier(table))[0]['count'] if table in columns else None
        archive.append(dict(model=model, table=table, rows=count, transient=model in TRANSIENT_MODELS))
    tables = [model.replace('.', '_') for model in ARCHIVE_MODELS]
    foreign_keys = query("""SELECT c.conname AS name, sn.nspname AS source_schema,
        s.relname AS source_table, tn.nspname AS target_schema, t.relname AS target_table,
        ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(attnum,ord)
              JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.ord) AS source_columns,
        pg_get_constraintdef(c.oid) AS definition, c.convalidated AS validated
        FROM pg_constraint c JOIN pg_class s ON s.oid=c.conrelid JOIN pg_namespace sn ON sn.oid=s.relnamespace
        JOIN pg_class t ON t.oid=c.confrelid JOIN pg_namespace tn ON tn.oid=t.relnamespace
        WHERE c.contype='f' AND ((sn.nspname=current_schema() AND s.relname=ANY(%s))
          OR (tn.nspname=current_schema() AND t.relname=ANY(%s))) ORDER BY sn.nspname,s.relname,c.conname
    """, [tables, tables])
    for fk in foreign_keys:
        source = identifier(fk['source_schema']) + '.' + identifier(fk['source_table'])
        where = ' AND '.join(identifier(name) + ' IS NOT NULL' for name in fk['source_columns'])
        fk['referencing_rows'] = query('SELECT COUNT(*) AS count FROM ' + source + ' WHERE ' + where)[0]['count']
        fk['external_inbound'] = fk['target_table'] in tables and fk['source_table'] not in tables

    fields = query("""SELECT f.id,f.model,f.name,f.ttype,f.relation,f.store,f.state,
        f.relation_table,f.column1,f.column2,
        ARRAY(SELECT d.module FROM ir_model_data d WHERE d.model='ir.model.fields' AND d.res_id=f.id ORDER BY d.module) AS metadata_modules
        FROM ir_model_fields f WHERE f.model=ANY(%s) OR f.relation=ANY(%s)
          OR (f.model=ANY(%s) AND (f.name LIKE 'quality_release_%%' OR f.name IN
            ('quality_stock_revision','quality_stock_release_plan_ids','stock_release_plan_ids','can_prepare_unit_stock_release')))
        ORDER BY f.model,f.name
    """, [list(ARCHIVE_MODELS + TRANSIENT_MODELS), list(ARCHIVE_MODELS + TRANSIENT_MODELS), list(LIVE_TABLES)])
    live_values = []
    for field in fields:
        table = LIVE_TABLES.get(field['model'])
        if table and field['store'] and field['name'] in columns.get(table, set()):
            name = identifier(field['name'])
            count = query('SELECT COUNT(*) AS count FROM ' + identifier(table) +
                          ' WHERE ' + name + " IS NOT NULL AND " + name + "::text NOT IN ('0','false','')")[0]['count']
            live_values.append(dict(model=field['model'], field=field['name'], nondefault_rows=count))
    relations = sorted({f['relation_table'] for f in fields if f['relation_table']})
    relation_counts = {name: query('SELECT COUNT(*) AS count FROM ' + identifier(name))[0]['count']
                       if name in columns else None for name in relations}
    tokens = sorted(set(ARCHIVE_MODELS + TRANSIENT_MODELS) | {f['name'] for f in fields if f['model'] in LIVE_TABLES})
    patterns = ['%' + value.replace('\\', '\\\\').replace('_', '\\_').replace('%', '\\%') + '%' for value in tokens]
    references = {}
    for table, text_fields, model_field in (
        ('ir_ui_view', ('arch_db',), 'model'),
        ('ir_act_window', ('domain', 'context'), 'res_model'),
        ('ir_act_server', ('code',), None), ('ir_filters', ('domain', 'context'), 'model_id'),
        ('ir_rule', ('domain_force',), None),
    ):
        if table not in columns:
            continue
        parts, params = [], []
        for name in text_fields:
            if name in columns[table]:
                parts.append(identifier(name) + '::text LIKE ANY(%s)')
                params.append(patterns)
        if model_field in columns[table]:
            parts.append(identifier(model_field) + '=ANY(%s)')
            params.append(list(ARCHIVE_MODELS + TRANSIENT_MODELS))
        if table in ('ir_act_server', 'ir_rule') and 'model_id' in columns[table]:
            parts.append('model_id IN (SELECT id FROM ir_model WHERE model=ANY(%s))')
            params.append(list(ARCHIVE_MODELS + TRANSIENT_MODELS))
        if parts:
            references[table] = records(table, 'id name model res_model inherit_id active', '(' + ' OR '.join(parts) + ')', params)
    references['acl'] = query("""SELECT a.id,a.name,m.model,a.group_id,a.perm_read,a.perm_write,a.perm_create,a.perm_unlink
        FROM ir_model_access a JOIN ir_model m ON m.id=a.model_id WHERE m.model=ANY(%s) ORDER BY m.model,a.id
    """, [list(ARCHIVE_MODELS + TRANSIENT_MODELS)])
    for table, model_field in (('ir_attachment', 'res_model'), ('mail_message', 'model'),
                               ('mail_followers', 'res_model'), ('mail_activity', 'res_model')):
        if model_field in columns.get(table, set()):
            references[table] = records(table, 'id res_id', identifier(model_field) + '=ANY(%s)', [list(ARCHIVE_MODELS)])
    references['external_xmlids'] = query("""SELECT module,name,model,res_id FROM ir_model_data
        WHERE module!='quality_vataga' AND (
            (model='ir.model' AND res_id IN (SELECT id FROM ir_model WHERE model=ANY(%s)))
            OR (model='ir.model.fields' AND res_id=ANY(%s))
            OR (model='ir.ui.view' AND res_id=ANY(%s))) ORDER BY module,name
    """, [list(ARCHIVE_MODELS + TRANSIENT_MODELS), [f['id'] for f in fields],
          [v['id'] for v in references.get('ir_ui_view', [])]])
    references['record_xmlids'] = query('SELECT module,name,model,res_id FROM ir_model_data WHERE model=ANY(%s) ORDER BY module,name',
                                        [list(ARCHIVE_MODELS + TRANSIENT_MODELS)])

    mo = records('mrp_production', 'id name state company_id product_id product_qty product_uom_id qty_producing qty_produced quality_transfer_controlled quality_transfer_revision quality_stock_revision',
                 'name=%s', [mo_name])
    mo_ids = [row['id'] for row in mo]
    graph = {'production': mo}
    for key, table, names, link in (
        ('workorders', 'mrp_workorder', 'id name state production_id qty_producing qty_produced', 'production_id'),
        ('runs', 'quality_production_run', 'id name production_id workorder_id origin_check_id quantity state', 'production_id'),
        ('units', 'quality_production_unit', 'id name production_id registration_run_id unit_number lot_id serial_lot_id', 'production_id'),
        ('admissions', 'quality_unit_admission', 'id check_id sample_id controlled_unit_id operation_run_id workorder_id production_id state', 'production_id'),
        ('transfers', 'quality_unit_transfer', 'id check_id controlled_unit_id production_id source_run_id source_workorder_id destination_workorder_id state', 'production_id'),
        ('executions', 'quality_unit_execution', 'id batch_id transfer_id controlled_unit_id workorder_id production_id state', 'production_id'),
    ):
        graph[key] = records(table, names, identifier(link) + '=ANY(%s)', [mo_ids]) if link in columns.get(table, set()) else []
    wo_ids = [row['id'] for row in graph['workorders']]
    run_ids = [row['id'] for row in graph['runs']]
    predicates, params = [], []
    for name, ids in (('production_id', mo_ids), ('workorder_id', wo_ids), ('operation_run_id', run_ids)):
        if name in columns.get('quality_check', set()):
            predicates.append(identifier(name) + '=ANY(%s)'); params.append(ids)
    graph['checks'] = records('quality_check', 'id name production_id workorder_id operation_run_id point_id quality_state in_process_sample_quantity',
                              '(' + ' OR '.join(predicates) + ')', params) if predicates else []
    check_ids = [row['id'] for row in graph['checks']]
    for key, table, names, link, ids in (
        ('samples', 'quality_check_sample', 'id quality_check_id sample_number controlled_unit_id sample_result visual_result x_was_reworked', 'quality_check_id', check_ids),
        ('alerts', 'quality_alert', 'id name check_id stage_id is_approved vataga_closed_at vataga_closed_by_id quality_release_scope_unit_id quality_release_scope_check_id', 'check_id', check_ids),
        ('timers', 'mrp_workcenter_productivity', 'id workorder_id quality_execution_batch_id employee_id date_start date_end duration', 'workorder_id', wo_ids),
    ):
        graph[key] = records(table, names, identifier(link) + '=ANY(%s)', [ids]) if link in columns.get(table, set()) else []
    if 'mrp_workorder_dependencies_rel' in columns:
        graph['workorder_dependencies'] = query('SELECT * FROM mrp_workorder_dependencies_rel WHERE workorder_id=ANY(%s) OR blocked_by_id=ANY(%s)', [wo_ids, wo_ids])
    graph['moves'] = records('stock_move', 'id name state production_id raw_material_production_id product_id product_uom product_uom_qty quantity picked',
        'production_id=ANY(%s) OR raw_material_production_id=ANY(%s)', [mo_ids, mo_ids])
    move_ids = [row['id'] for row in graph['moves']]
    graph['move_lines'] = records('stock_move_line', 'id move_id product_id product_uom_id quantity lot_id', 'move_id=ANY(%s)', [move_ids])
    return dict(database=database, user=user, read_only=True, archive_models=archive,
                all_nine_present_and_empty=all(row['rows'] == 0 for row in archive if not row['transient']),
                foreign_keys=foreign_keys, orm_fields=fields, live_stored_values=live_values,
                relation_tables=relation_counts, metadata_candidates=references, mo_graph=graph,
                mo_identity_unambiguous=len(mo) == 1,
                unavailable_columns=unavailable, destructive_cleanup_authorized=False,
                warning='Per-database evidence only. Review installed addon code, polymorphic references, metadata candidates and every target database before any schema removal.')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--read-only-inventory', action='store_true', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--host', default=os.environ.get('PGHOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('PGPORT', '5432')))
    parser.add_argument('--user', default=os.environ.get('PGUSER'), required=not os.environ.get('PGUSER'))
    parser.add_argument('--mo', default='CAB/MO/05095')
    args = parser.parse_args()
    connection = psycopg2.connect(dbname=args.database, host=args.host, port=args.port, user=args.user)
    try:
        connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with connection.cursor() as cr:
            cr.execute("SET LOCAL statement_timeout='30s'")
            result = collect_inventory(cr, args.mo)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        connection.rollback()
        connection.close()


if __name__ == '__main__':
    main()
