"""Opt-in native Community probe; always creates and drops its OWN database.

Uses unchanged shipped kernel sources in a temporary technical addon. There
are no fake quality models, stock mocks, existing DB option or Docker calls.
PGUSER/PGPASSWORD supply credentials for a disposable local PostgreSQL server.
"""

import argparse
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

import psycopg2
from psycopg2 import sql


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--odoo-source', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--run-disposable-tests', action='store_true', required=True)
    args = parser.parse_args()
    source = args.odoo_source.resolve()
    root = Path(__file__).resolve().parents[1]
    database = 'qunit_kernel_' + uuid.uuid4().hex[:16]
    connection = psycopg2.connect(dbname='postgres', host='127.0.0.1', port=args.port,
                                 user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])
    connection.autocommit = True
    created = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
            created = True
        print('DISPOSABLE DATABASE:', database, '127.0.0.1:', args.port, flush=True)
        with tempfile.TemporaryDirectory(prefix='qunit_kernel_') as directory:
            temp = Path(directory)
            addon = temp / 'quality_stock_core_probe'
            (addon / 'models').mkdir(parents=True)
            (addon / 'tests').mkdir()
            (addon / 'security').mkdir()
            (addon / '__manifest__.py').write_text(repr({
                'name': 'Disposable native QUNIT kernel probe', 'version': '17.0.2.40',
                'depends': ['mrp_account'], 'license': 'LGPL-3',
                'data': ['security/stock_posting.xml', 'security/ir.model.access.csv'],
            }), encoding='utf-8')
            (addon / '__init__.py').write_text('from . import models\nfrom . import tests\n', encoding='utf-8')
            for filename in ('stock_posting_core.py', 'stock_cost_allocator.py', 'stock_release_plan_utils.py', 'quality_release_access.py'):
                shutil.copyfile(root / 'models' / filename, addon / 'models' / filename)
            (addon / 'models' / '__init__.py').write_text('from . import stock_posting_core\n', encoding='utf-8')
            for filename in ('test_stock_posting_core.py', 'test_stock_release_native_contract.py'):
                shutil.copyfile(root / 'tests' / filename, addon / 'tests' / filename)
            (addon / 'tests' / '__init__.py').write_text('from . import test_stock_release_native_contract\nfrom . import test_stock_posting_core\n', encoding='utf-8')
            shutil.copyfile(root / 'security' / 'stock_posting.xml', addon / 'security' / 'stock_posting.xml')
            with (root / 'security' / 'ir.model.access.csv').open(encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            with (addon / 'security' / 'ir.model.access.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(row for row in rows if row['id'] in {
                    'access_stock_cost_scope', 'access_stock_posting', 'access_stock_posting_line', 'access_stock_cost_allocation',
                })
            result = subprocess.run([
                sys.executable, str(source / 'odoo-bin'), '-d', database,
                '--db_host=127.0.0.1', '--db_port=' + str(args.port), '--db_user=' + os.environ['PGUSER'],
                '--db_password=' + os.environ['PGPASSWORD'],
                '--addons-path=' + ','.join(str(path) for path in (source / 'odoo/addons', source / 'addons', temp)),
                '--data-dir=' + str(temp / 'data'), '-i', 'quality_stock_core_probe', '--without-demo=all',
                '--no-http', '--http-interface=127.0.0.1', '--http-port=0', '--max-cron-threads=0',
                '--test-enable', '--test-tags=/quality_stock_core_probe',
                '--stop-after-init', '--log-level=test',
            ], cwd=source, check=False)
            return result.returncode
    finally:
        if created:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
            print('DROPPED DISPOSABLE DATABASE:', database, flush=True)
        connection.close()


if __name__ == '__main__':
    sys.exit(main())
