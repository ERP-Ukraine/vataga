"""Exercise the ACTUAL _vataga_lock SQL against a disposable local PostgreSQL.

Only the SQL lock protocol is tested. This is not an Enterprise ORM/RFQ test.
Creates and removes its own random schema; never operates on an Odoo table.
"""
import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from collections import defaultdict
import uuid
import sys

import psycopg2
from psycopg2 import errors, sql
from psycopg2.extensions import parse_dsn


def load_lock(SQL):
    path = Path(__file__).resolve().parents[1] / 'models/approval_request.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ApprovalRequest')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_vataga_lock')
    namespace = {'SQL': SQL}
    internal_path = path.parents[1] / 'internal.py'
    internal_tree = ast.parse(internal_path.read_text(encoding='utf-8'))
    helper = next(n for n in internal_tree.body if isinstance(n, ast.FunctionDef) and n.name == 'refreshable_fields')
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(internal_path), 'exec'), namespace)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['_vataga_lock']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dsn', required=True, help='Disposable LOCAL PostgreSQL only')
    parser.add_argument('--odoo-source', type=Path)
    args = parser.parse_args()
    if args.odoo_source:
        sys.path.insert(0, str(args.odoo_source))
    from odoo.tools import SQL
    options = parse_dsn(args.dsn)
    assert options.get('host') in ('127.0.0.1', 'localhost'), 'LOCAL test cluster required'
    assert options.get('port') == '55491', 'Dedicated Vataga test port required'
    schema = 'vataga_lock_' + uuid.uuid4().hex
    lock = load_lock(SQL)

    def connect():
        connection = psycopg2.connect(args.dsn)
        connection.set_session(isolation_level='REPEATABLE READ')
        return connection

    def cursor(connection):
        result = connection.cursor()
        result.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        return result

    def request(cur):
        class CursorAdapter:
            def execute(self, statement, params=None):
                if isinstance(statement, SQL):
                    return cur.execute(statement.code, statement.params)
                return cur.execute(statement, params)
        class Field:
            name = 'product_line_ids'
        class RecordSet(list):
            def flush_recordset(self, names=None):
                pass
            def invalidate_recordset(self, names=None):
                pass
        env = SimpleNamespace(cr=CursorAdapter(), is_protected=lambda field, record: False,
                              protected=lambda field: [])
        pool = SimpleNamespace(field_inverses=defaultdict(tuple))
        lines = RecordSet()
        lines._fields, lines.env, lines.pool = {}, env, pool
        records = RecordSet()
        records.append(records)
        records.id, records.ids, records._table = 1, [1], 'approval_request'
        records._fields = {'product_line_ids': Field()}
        records.env, records.pool = env, pool
        records._vataga_check_access = lambda: None
        records.product_line_ids = lines
        return records

    admin = connect()
    try:
        with admin:
            cur = admin.cursor()
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            cur = cursor(admin)
            cur.execute('CREATE TABLE approval_request (id INT PRIMARY KEY, vataga_lock_revision INT DEFAULT 0, processed BOOLEAN DEFAULT FALSE)')
            cur.execute('CREATE TABLE protocol_line (id INT PRIMARY KEY, product_id INT)')
            cur.execute('CREATE TABLE protocol_operations (id SERIAL PRIMARY KEY, kind TEXT)')
            cur.execute('INSERT INTO approval_request(id) VALUES (1)')
            cur.execute('INSERT INTO protocol_line(id) VALUES (1)')
        for kind in ('rfq-marker', 'child-only-fill'):
            with admin:
                cur = cursor(admin)
                cur.execute('UPDATE approval_request SET processed=FALSE')
                cur.execute('UPDATE protocol_line SET product_id=NULL')
                cur.execute('DELETE FROM protocol_operations')
            barrier = Barrier(2)

            def worker():
                connection = connect()
                retries = 0
                try:
                    for attempt in range(5):
                        try:
                            with connection:
                                cur = cursor(connection)
                                cur.execute('SELECT processed FROM approval_request WHERE id=1')
                                if attempt == 0:
                                    barrier.wait(timeout=10)
                                lock(request(cur))
                                if kind == 'rfq-marker':
                                    cur.execute('SELECT processed FROM approval_request WHERE id=1')
                                else:
                                    cur.execute('SELECT product_id IS NOT NULL FROM protocol_line WHERE id=1')
                                if cur.fetchone()[0]:
                                    return 'blocked', retries
                                cur.execute('INSERT INTO protocol_operations(kind) VALUES (%s)', [kind])
                                if kind == 'rfq-marker':
                                    cur.execute('UPDATE approval_request SET processed=TRUE WHERE id=1')
                                else:
                                    cur.execute('UPDATE protocol_line SET product_id=42 WHERE id=1')
                            return 'created', retries
                        except errors.SerializationFailure:
                            retries += 1
                    raise AssertionError('Retry budget exhausted')
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(worker) for _ in range(2)]
                outcomes = [future.result(timeout=20) for future in futures]
            assert sorted(value for value, _ in outcomes) == ['blocked', 'created'], outcomes
            assert sum(retries for _, retries in outcomes) >= 1, outcomes
            with admin:
                cur = cursor(admin)
                cur.execute('SELECT count(*) FROM protocol_operations')
                assert cur.fetchone()[0] == 1
            print('PASS:', kind, 'concurrent repeatable-read calls:', outcomes)
        with admin:
            cur = cursor(admin)
            cur.execute('UPDATE approval_request SET processed=FALSE')
            cur.execute('DELETE FROM protocol_operations')
        failing = connect()
        try:
            try:
                with failing:
                    cur = cursor(failing)
                    lock(request(cur))
                    cur.execute("INSERT INTO protocol_operations(kind) VALUES ('failure')")
                    cur.execute('UPDATE approval_request SET processed=TRUE')
                    raise RuntimeError('Injected failure')
            except RuntimeError:
                pass
            with admin:
                cur = cursor(admin)
                cur.execute('SELECT processed FROM approval_request WHERE id=1')
                assert not cur.fetchone()[0]
                cur.execute('SELECT count(*) FROM protocol_operations')
                assert cur.fetchone()[0] == 0
            print('PASS: transaction failure rolls back operations and processed marker')
        finally:
            failing.close()
    finally:
        with admin:
            admin.cursor().execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        admin.close()


if __name__ == '__main__':
    main()
