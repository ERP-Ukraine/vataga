"""Behavioral native-call contracts; these are not Enterprise Stock ORM tests."""
import ast
import math
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError

ROOT = Path(__file__).resolve().parents[1]


def capability():
    tree = ast.parse((ROOT / 'models/native_backorder.py').read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if (
        isinstance(node, ast.ImportFrom) and node.module in ('contextlib', 'contextvars', 'dataclasses')
        or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '_OPERATION' for target in node.targets)
        or isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in (
            'NativeBackorderOperation', 'native_operation', 'native_operation_allows'))]
    scope = {'__name__': __name__}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'shipped_capability', 'exec'), scope)
    return scope


class Units(list):
    @property
    def ids(self):
        return [unit.id for unit in self]

    def sorted(self, field):
        return Units(sorted(self, key=lambda unit: getattr(unit, field)))


class NativeParent(list):
    def __init__(self, records=(), env=None):
        super().__init__(records)
        self.env = env

    @property
    def id(self):
        return self[0].id

    def filtered(self, predicate):
        return type(self)((row for row in self if predicate(row)), self.env)

    def __sub__(self, other):
        return self.filtered(lambda row: row not in other)

    def __and__(self, other):
        return self.filtered(lambda row: row in other)

    def browse(self, ids):
        ids = [ids] if isinstance(ids, int) else ids
        return type(self)((self.env.rows[index] for index in ids), self.env)

    def check_access_rights(self, operation):
        pass

    def check_access_rule(self, operation):
        pass

    def _lock_quality_transfer_scope(self):
        for row in self:
            row.revision += 1

    def button_mark_done(self):
        self.env.native_calls += 1
        if self.env.dialog:
            return self.env.dialog
        if self.env.fail_stock:
            self.env.stock += 1
            raise UserError('Stock failed')
        for row in self:
            row.state, row.qty_produced = 'done', row.product_qty
            self.env.stock += row.product_qty
        return self.env.result


class TestStandardNativeCompletion(unittest.TestCase):
    def setUp(self):
        self.scope = capability()
        class Environment(NS):
            def __getitem__(env, name):
                return NS(browse=lambda ids: Units(NS(id=value) for value in ids))
        self.env = Environment(rows={}, native_calls=0, facts=[], stock=0, dialog=False,
                      fail_stock=False, fail_facts=False, result={'native': 'result'}, context={})
        @contextmanager
        def savepoint():
            snapshot = {key: (row.state, row.qty_produced, row.revision) for key, row in self.env.rows.items()}
            stock, facts = self.env.stock, list(self.env.facts)
            try:
                yield
            except Exception:
                for key, values in snapshot.items():
                    row = self.env.rows[key]
                    row.state, row.qty_produced, row.revision = values
                self.env.stock, self.env.facts = stock, facts
                raise
        self.env.cr = NS(savepoint=savepoint)
        self.Model = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
            {'button_mark_done'}, NativeParent, **{key: self.scope[key] for key in (
                'NativeBackorderOperation', 'native_operation_allows')})
        self.row = self.add(1)
        self.records = self.Model([self.row], self.env)

    def add(self, index, required=True, state='to_close', controlled=False):
        units = Units([NS(id=index * 100 + value) for value in (1, 2)])
        row = NS(id=index, env=self.env, quality_unit_route_required=required,
                 quality_transfer_controlled=controlled, state=state, revision=0,
                 product_qty=2, qty_produced=2 if state == 'done' else 0,
                 lot_producing_id=False, _full_release_units=Mock(return_value=units),
                 _validate_quality_full_batch=Mock(), _check_standard_release_quantity=Mock(),
                 _validate_full_release_lot=Mock())
        def facts(selected):
            self.assertEqual(tuple(selected.ids), self.scope['native_operation']().standard_full[index])
            self.env.facts.extend(selected.ids)
            if self.env.fail_facts:
                raise UserError('Fact failed')
        row._record_standard_stock_facts = facts
        self.env.rows[index] = row
        return row

    def test_native_button_result_is_returned_and_facts_follow_success(self):
        self.assertIs(self.records.button_mark_done(), self.env.result)
        self.assertEqual(self.env.native_calls, 1)
        self.assertEqual(self.env.facts, [101, 102])
        self.assertEqual(self.row.state, 'done')
        self.assertIsNone(self.scope['native_operation']())
        self.assertEqual(self.row._validate_quality_full_batch.call_args.kwargs,
                         {'standard': True, 'posted': True})

    def test_native_dialog_creates_no_stock_fact_and_no_permanent_capability(self):
        self.env.dialog = {'res_model': 'mrp.consumption.warning'}
        self.assertIs(self.records.button_mark_done(), self.env.dialog)
        self.assertEqual((self.env.stock, self.env.facts), (0, []))
        self.assertIsNone(self.scope['native_operation']())

    def test_quality_rejection_precedes_native_stock(self):
        self.row._validate_quality_full_batch.side_effect = UserError('Pending QC')
        with self.assertRaises(UserError):
            self.records.button_mark_done()
        self.assertEqual(self.env.native_calls, 0)
        self.assertEqual((self.env.stock, self.env.facts, self.row.revision), (0, [], 0))

    def test_stock_error_rolls_back_native_changes(self):
        self.env.fail_stock = True
        with self.assertRaisesRegex(UserError, 'Stock failed'):
            self.records.button_mark_done()
        self.assertEqual((self.row.state, self.env.stock, self.env.facts), ('to_close', 0, []))
        self.assertIsNone(self.scope['native_operation']())

    def test_fact_error_rolls_back_posted_state_stock_and_inserted_facts(self):
        self.env.fail_facts = True
        with self.assertRaisesRegex(UserError, 'Fact failed'):
            self.records.button_mark_done()
        self.assertEqual((self.row.state, self.row.qty_produced, self.env.stock, self.env.facts),
                         ('to_close', 0, 0, []))
        self.assertIsNone(self.scope['native_operation']())

    def test_repeat_done_is_a_read_only_no_op(self):
        self.records.button_mark_done()
        before = (self.env.native_calls, self.row.revision, list(self.env.facts), self.env.stock)
        self.assertTrue(self.records.button_mark_done())
        self.assertEqual(before, (self.env.native_calls, self.row.revision, self.env.facts, self.env.stock))

    def test_already_done_mo_without_facts_is_not_backfilled(self):
        self.row.state = 'done'
        self.assertTrue(self.records.button_mark_done())
        self.assertEqual((self.env.native_calls, self.row.revision, self.env.facts), (0, 0, []))

    def test_ordinary_mo_keeps_native_button_without_qunit_requirements(self):
        self.row.quality_unit_route_required = False
        self.records.button_mark_done()
        self.row._full_release_units.assert_not_called()
        self.assertEqual(self.env.facts, [])

    def test_bulk_call_records_each_validated_mo_without_mixing_units(self):
        second = self.add(2)
        self.records = self.Model([self.row, second], self.env)
        self.records.button_mark_done()
        self.assertEqual(self.env.facts, [101, 102, 201, 202])
        self.assertEqual(self.env.native_calls, 1)

    def test_forged_rpc_context_cannot_skip_validation(self):
        self.env.context = {'quality_full_release_test_enabled': True, 'skip_quality': True}
        self.test_quality_rejection_precedes_native_stock()


class TestStandardReleaseQuantity(unittest.TestCase):
    def setUp(self):
        self.production = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                                        {'_check_standard_release_quantity'})()
        self.production.ensure_one = Mock()
        self.production.product_qty, self.production.qty_producing = 2, 2
        self.production.product_id = NS(tracking='none')

    def test_full_none_lot_and_zero_native_quantity_are_allowed(self):
        for tracking, producing in (('none', 2), ('lot', 2), ('lot', 0)):
            self.production.product_id.tracking, self.production.qty_producing = tracking, producing
            self.production._check_standard_release_quantity([1, 2])

    def test_partial_quantity_and_missing_units_cannot_trigger_backorder(self):
        for producing, units in ((1, [1, 2]), (2, [1]), (-1, [1, 2])):
            self.production.qty_producing = producing
            with self.assertRaises(UserError):
                self.production._check_standard_release_quantity(units)

    def test_single_serial_is_supported_and_multi_serial_stays_closed(self):
        self.production.product_id.tracking = 'serial'
        with self.assertRaises(UserError):
            self.production._check_standard_release_quantity([1, 2])
        self.production.product_qty = self.production.qty_producing = 1
        self.production._check_standard_release_quantity([1])


class TestActualStockLineMapping(unittest.TestCase):
    def setUp(self):
        self.production = shipped_class('models/quality_full_release.py', 'QualityFullReleaseProduction',
                                        {'_record_standard_stock_facts'}, math=math,
                                        _read=lambda rows: rows)()
        production = self.production
        production.ensure_one = Mock()
        production.id, production.product_id, production.company_id = 1, NS(id=2, tracking='none'), 3
        production.product_uom_id, production.location_dest_id, production.lot_producing_id = 4, 5, False
        production._validate_full_release_lot = Mock()
        self.units = Units([NS(id=101, lot_id=False), NS(id=102, lot_id=False)])
        self.line = NS(id=7, product_id=production.product_id, company_id=3,
                       product_uom_id=4, location_dest_id=5, location_id=NS(usage='production'),
                       quantity=2, lot_id=False)
        self.facts = Mock(side_effect=lambda values: values)
        lines = [self.line]
        class Environment:
            cr = NS(execute=Mock(), fetchall=lambda: [(line.id,) for line in lines])
            def __getitem__(env, name):
                if name == 'quality.unit.stock.fact':
                    return NS(create=self.facts)
                return NS(flush_model=Mock(), browse=lambda ids: lines)
        production.env = Environment()

    def test_two_qunit_use_one_real_lot_line_without_changing_identity(self):
        self.production.product_id.tracking = 'lot'
        self.production.lot_producing_id = self.line.lot_id = 55
        values = self.production._record_standard_stock_facts(self.units)
        self.assertEqual([row['unit_id'] for row in values], [101, 102])
        self.assertEqual([row['move_line_id'] for row in values], [7, 7])
        self.assertTrue(all(row['production_id'] == 1 for row in values))
        self.assertTrue(all(unit.lot_id is False for unit in self.units))

    def test_none_and_one_serial_have_exact_actual_line_correspondence(self):
        self.assertEqual(len(self.production._record_standard_stock_facts(self.units)), 2)
        self.production.product_id.tracking = 'serial'
        self.production.lot_producing_id = self.line.lot_id = 66
        self.line.quantity = 1
        values = self.production._record_standard_stock_facts(Units(self.units[:1]))
        self.assertEqual(values[0]['unit_id'], 101)
        self.assertEqual(values[0]['move_line_id'], 7)

    def test_missing_excess_fractional_negative_and_nonfinite_stock_are_rejected(self):
        for quantity in (0, 1, 3, 1.5, -1, math.nan, math.inf):
            with self.subTest(quantity=quantity):
                self.line.quantity = quantity
                with self.assertRaises(UserError):
                    self.production._record_standard_stock_facts(self.units)
        self.facts.assert_not_called()

    def test_wrong_company_product_uom_destination_and_source_are_rejected(self):
        for field, wrong in (('company_id', 99), ('product_id', object()),
                             ('product_uom_id', 99), ('location_dest_id', 99),
                             ('location_id', NS(usage='internal'))):
            with self.subTest(field=field):
                previous = getattr(self.line, field)
                setattr(self.line, field, wrong)
                with self.assertRaises(UserError):
                    self.production._record_standard_stock_facts(self.units)
                setattr(self.line, field, previous)
        self.facts.assert_not_called()

    def test_other_lot_cannot_be_silently_assigned_to_units(self):
        self.production.product_id.tracking = 'lot'
        self.production.lot_producing_id, self.line.lot_id = 55, 99
        with self.assertRaises(UserError):
            self.production._record_standard_stock_facts(self.units)
        self.facts.assert_not_called()

    def test_untracked_product_does_not_accept_a_hidden_lot(self):
        self.line.lot_id = 55
        with self.assertRaises(UserError):
            self.production._record_standard_stock_facts(self.units)
        self.facts.assert_not_called()


if __name__ == '__main__':
    unittest.main()
