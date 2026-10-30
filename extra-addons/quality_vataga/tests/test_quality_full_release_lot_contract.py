"""Lot policy tests of shipped code; no claim of actual ORM stock posting."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_full_release_contract import Rows, Record
    from .test_quality_defect_contract import shipped_class, UserError
except ImportError:
    from test_quality_full_release_contract import Rows, Record
    from test_quality_defect_contract import shipped_class, UserError

ROOT = Path(__file__).resolve().parents[1]


class Lot(Record):
    def exists(self):
        return self
    def __len__(self):
        return 1


class EmptyLot(Rows):
    def exists(self):
        return self


class TestFullReleaseLotContract(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                            {'_validate_full_release_lot'}, _read=lambda records: records)
        self.production = cls()
        self.production.ensure_one = Mock()
        self.product, self.company = Record(id=1, tracking='lot'), Record(id=2)
        self.production.product_id, self.production.company_id = self.product, self.company
        self.production.lot_producing_id = False
        self.production.move_finished_ids = Rows()
        self.lot = Lot(id=7, product_id=self.product, company_id=self.company)
        self.units = Rows([Record(id=index, lot_id=False, display_name=str(index)) for index in (11, 12)])
        class Env:
            def __getitem__(self, name):
                return EmptyLot()
        self.production.env = Env()

    def test_actual_lot_is_valid_without_rewriting_qunit_lot_metadata(self):
        self.assertIs(self.production._validate_full_release_lot(self.units, self.lot), self.lot)
        self.assertTrue(all(unit.lot_id is False for unit in self.units))
        self.assertFalse(self.production.lot_producing_id)

    def test_missing_or_deleted_lot_is_rejected(self):
        for lot in (False, EmptyLot()):
            with self.subTest(lot=lot), self.assertRaises(UserError):
                self.production._validate_full_release_lot(self.units, lot)

    def test_foreign_product_or_company_is_rejected(self):
        for values in ({'product_id': Record(id=99), 'company_id': self.company},
                       {'product_id': self.product, 'company_id': Record(id=99)}):
            with self.assertRaises(UserError):
                self.production._validate_full_release_lot(self.units, Lot(id=99, **values))

    def test_mo_or_qunit_bound_to_another_lot_is_not_relabelled(self):
        self.production.lot_producing_id = Lot(id=99)
        with self.assertRaises(UserError):
            self.production._validate_full_release_lot(self.units, self.lot)
        self.production.lot_producing_id = False
        self.units[0].lot_id = Lot(id=99)
        with self.assertRaises(UserError):
            self.production._validate_full_release_lot(self.units, self.lot)

    def test_existing_positive_stock_line_with_other_lot_is_not_relabelled(self):
        line = Record(id=8, quantity=2, lot_id=Lot(id=99))
        self.production.move_finished_ids = Rows([Record(id=9, product_id=self.product, state='assigned', move_line_ids=Rows([line]))])
        with self.assertRaises(UserError):
            self.production._validate_full_release_lot(self.units, self.lot)

    def test_native_precondition_accepts_only_full_lot_batch_and_keeps_serial_closed(self):
        tree = ast.parse((ROOT / 'models/native_backorder.py').read_text(encoding='utf-8'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == '_native_quality_complete')
        condition = next(node.test for node in method.body if isinstance(node, ast.If) and 'create_backorder' in ast.unparse(node.test))
        predicate = compile(ast.Expression(body=condition), 'native_lot_guard', 'eval')
        production = NS(qty_produced=0, state='to_close', picking_type_id=NS(create_backorder='ask'),
                        product_id=NS(type='product', tracking='lot'), move_byproduct_ids=False, move_raw_ids=Rows())
        self.assertFalse(eval(predicate, {'self': production, 'operation': NS(quantity=2), 'original_quantity': 2}))
        self.assertTrue(eval(predicate, {'self': production, 'operation': NS(quantity=1), 'original_quantity': 2}))
        production.product_id.tracking = 'serial'
        self.assertTrue(eval(predicate, {'self': production, 'operation': NS(quantity=2), 'original_quantity': 2}))

    def test_fact_lot_guard_requires_real_matching_full_batch_line(self):
        tree = ast.parse((ROOT / 'models/quality_native_backorder.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'QualityUnitStockFact')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'create')
        branch = next(node for node in ast.walk(method) if isinstance(node, ast.If) and ast.unparse(node.test) == "unit.product_id.tracking == 'lot'")
        code = compile(ast.Module(body=branch.body, type_ignores=[]), 'actual_fact_lot_guard', 'exec')
        production = NS(product_qty=2, lot_producing_id=self.lot)
        unit = NS(product_id=self.product, company_id=self.company, lot_id=False)
        operation = NS(quantity=2, remaining_ids=(), standard_full={})
        line = NS(lot_id=self.lot)
        scope = {'production': production, 'unit': unit, 'operation': operation, 'line': line, 'allowed': (),
                 'partial_fact': False,
                 '_read': lambda record: record, 'ValidationError': UserError, '_': lambda text: text}
        exec(code, scope)
        for candidate in (False, Lot(id=99, product_id=self.product, company_id=self.company)):
            line.lot_id = candidate
            with self.assertRaises(UserError):
                exec(code, scope)
        line.lot_id = self.lot
        operation.quantity = 1
        with self.assertRaises(UserError):
            exec(code, scope)


if __name__ == '__main__':
    unittest.main()
