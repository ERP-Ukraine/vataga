"""Local checks; definition imports do NOT constitute Enterprise ORM tests."""
import argparse
import ast
import csv
import importlib
from pathlib import Path
import sys
import unittest

from lxml import etree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--odoo-source', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python_files = list(root.rglob('*.py'))
    xml_files = list(root.rglob('*.xml'))
    for path in python_files:
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    ids = set()
    for path in xml_files:
        document = etree.parse(str(path))
        assert not document.xpath('//*[@attrs or @states]'), path
        for record in document.xpath('//*[@id]'):
            xml_id = record.attrib['id']
            assert xml_id not in ids, xml_id
            ids.add(xml_id)
    manifest = ast.literal_eval((root / '__manifest__.py').read_text(encoding='utf-8'))
    for name in manifest['data']:
        assert (root / name).is_file(), name
    for names in manifest['assets'].values():
        for name in names:
            assert (root.parent / name).is_file(), name
    with (root / 'security/ir.model.access.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]['group_id:id'] == 'purchase.group_purchase_user'
    print(f'PASS: {len(python_files)} Python and {len(xml_files)} XML parsed; manifest, XML IDs and wizard ACL checked')
    from babel.messages.pofile import read_po
    with (root / 'i18n/uk.po').open('rb') as stream:
        catalog = read_po(stream, locale='uk')
    assert not list(catalog.check())
    print('PASS: Ukrainian PO catalog parsed')
    if not args.odoo_source:
        return
    sys.path.insert(0, str(args.odoo_source))
    import odoo
    from odoo.tools import config
    from odoo.modules.module import initialize_sys_path, get_module_path
    config['addons_path'] = ','.join((str(args.odoo_source / 'addons'), str(root.parent)))
    initialize_sys_path()
    importlib.import_module('odoo.addons.approvals_purchase_vataga')
    tests = importlib.import_module('odoo.addons.approvals_purchase_vataga.tests')
    loader = unittest.TestLoader()
    count = sum(loader.loadTestsFromModule(getattr(tests, name)).countTestCases()
                for name in ('test_purchase_approval', 'test_purchase_security',
                             'test_purchase_rfq', 'test_purchase_registry', 'test_purchase_rpc'))
    print('PASS: real Odoo', odoo.release.version, 'model/wizard definitions imported;', count, 'ORM tests collected')
    missing = [name for name in ('approvals', 'approvals_purchase')
               if not get_module_path(name, display_warning=False)]
    if missing:
        print('BLOCKED: Enterprise ORM execution; missing installed source:', ', '.join(missing))


if __name__ == '__main__':
    main()
