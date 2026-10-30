"""Opt-in real ORM adapter tests: own DB, no Docker and no existing DB option."""
import argparse
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
    parser.add_argument('--test-tags', default='/quality_partial_probe:TestNativePartialAdapter,'
                        '/quality_partial_probe:TestNativeBackorderBridge,/quality_partial_probe:TestNativePartialProduction')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = args.odoo_source.resolve()
    database = 'qunit_partial_' + uuid.uuid4().hex[:16]
    connection = psycopg2.connect(dbname='postgres', host='127.0.0.1', port=args.port,
                                 user=os.environ['PGUSER'], password=os.environ['PGPASSWORD'])
    connection.autocommit = True
    created = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        created = True
        print('DISPOSABLE DATABASE:', database, '127.0.0.1:', args.port, flush=True)
        with tempfile.TemporaryDirectory(prefix='qunit_partial_') as directory:
            temp = Path(directory)
            addon = temp / 'quality_partial_probe'
            for part in ('models', 'tests', 'security'):
                (addon / part).mkdir(parents=True)
            (addon / '__manifest__.py').write_text(repr({
                'name': 'Disposable incremental native MRP tests', 'version': '17.0.2.62',
                'license': 'LGPL-3', 'depends': ['mrp_account'],
                'data': ['security/ir.model.access.csv', 'security/rules.xml'],
            }), encoding='utf-8')
            (addon / '__init__.py').write_text('from . import models\nfrom . import tests\n', encoding='utf-8')
            for name in ('native_backorder.py', 'native_partial_release.py'):
                shutil.copyfile(root / 'models' / name, addon / 'models' / name)
            (addon / 'models/__init__.py').write_text('from . import native_backorder\nfrom . import native_partial_release\n', encoding='utf-8')
            for name in ('test_stock_release_native_contract.py', 'test_native_partial_adapter.py', 'test_native_backorder_bridge.py'):
                shutil.copyfile(root / 'tests' / name, addon / 'tests' / name)
            (addon / 'tests/__init__.py').write_text('from . import test_stock_release_native_contract\n'
                                                   'from . import test_native_partial_adapter\nfrom . import test_native_backorder_bridge\n', encoding='utf-8')
            names = ('release', 'unit', 'cost')
            (addon / 'security/ir.model.access.csv').write_text(
                'id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink\n' + ''.join(
                    f'access_partial_{n},partial {n},model_quality_native_partial_{n},mrp.group_mrp_user,1,0,1,0\n'
                    for n in names), encoding='utf-8')
            (addon / 'security/rules.xml').write_text('<odoo>' + ''.join(
                f'<record id="partial_{n}_company" model="ir.rule"><field name="name">partial {n} company</field>'
                f'<field name="model_id" ref="model_quality_native_partial_{n}"/>'
                '<field name="domain_force">[("company_id", "in", company_ids)]</field></record>'
                for n in names) + '</odoo>', encoding='utf-8')
            environment = dict(os.environ, QUNIT_DISPOSABLE_TEST_DATABASE=database, PYTHONUTF8='1')
            result = subprocess.run([
                sys.executable, str(source / 'odoo-bin'), '-d', database,
                '--db_host=127.0.0.1', '--db_port=' + str(args.port), '--db_user=' + os.environ['PGUSER'],
                '--db_password=' + os.environ['PGPASSWORD'],
                '--addons-path=' + ','.join(str(p) for p in (source / 'odoo/addons', source / 'addons', temp)),
                '--data-dir=' + str(temp / 'data'), '-i', 'quality_partial_probe', '--without-demo=all',
                '--no-http', '--http-interface=127.0.0.1', '--http-port=0', '--max-cron-threads=0',
                '--test-enable', '--test-tags=' + args.test_tags, '--stop-after-init', '--log-level=warn',
                '--log-handler=odoo.tests:INFO', '--log-handler=odoo.service.server:INFO',
                '--log-handler=odoo.addons.quality_partial_probe.tests:INFO',
            ], cwd=source, env=environment, check=False)
            return result.returncode
    finally:
        if created:
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
            print('DROPPED DISPOSABLE DATABASE:', database, flush=True)
        connection.close()


if __name__ == '__main__':
    sys.exit(main())
