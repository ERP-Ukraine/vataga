"""Execute shipped actual-batch gates without Odoo; no stock/ORM claims."""
import math
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

try:
    from .test_quality_defect_contract import shipped_class, UserError, ROOT
except ImportError:
    from test_quality_defect_contract import shipped_class, UserError, ROOT


class Row(NS):
    def __eq__(self, other):
        return [self.id] == other.ids if isinstance(other, Rows) else self is other

    def __ne__(self, other):
        return not self == other


class Rows(list):
    @property
    def ids(self):
        return [row.id for row in self]

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return Rows(value) if isinstance(key, slice) else value

    def __getattr__(self, name):
        if name == '_assert_not_stock_posted':
            return lambda: None
        values = []
        for row in self:
            value = getattr(row, name)
            for item in value if isinstance(value, Rows) else [value]:
                if not any(item is existing for existing in values):
                    values.append(item)
        if all(isinstance(value, Row) for value in values):
            return Rows(values)
        if len(self) == 1:
            return getattr(self[0], name)
        raise AttributeError(name)

    def sorted(self, key):
        return Rows(sorted(self, key=key))

    def filtered(self, predicate):
        return Rows(row for row in self if predicate(row))


class TestActualBatchValidation(unittest.TestCase):
    def setUp(self):
        cls = shipped_class('models/quality_actual_batch_release.py', 'QualityActualBatchProduction',
            {'_validate_quality_actual_batch'}, math=math, _read=lambda rows: rows)
        p = self.production = cls()
        p.ensure_one = Mock()
        p.id, p.state, p.qty_produced, p.qty_producing, p.product_qty = 1, 'progress', 0, 3, 5
        p.quality_unit_route_required = p.quality_transfer_controlled = p.allow_workorder_dependencies = False
        p.company_id, p.product_uom_id = object(), object()
        p.product_id = NS(uom_id=p.product_uom_id, tracking='none')
        p.env = NS(companies=[p.company_id], ref=lambda xmlid: p.product_uom_id)
        p.location_dest_id = NS(usage='internal', scrap_location=False, company_id=p.company_id)
        self.first = Row(id=10, state='done', qty_produced=3, operation_id=NS(sequence=1),
                         display_name='Нарізка', time_ids=Rows(), _quality_native_history=lambda: Rows())
        self.last = Row(id=11, state='progress', qty_produced=3, operation_id=NS(sequence=2),
                        display_name='Контроль', time_ids=Rows())
        self.units = Rows(Row(id=index, company_id=p.company_id, product_id=p.product_id,
                             _quality_current_production=lambda: p) for index in (101, 102, 103))
        self.history = Rows(Row(id=index, state='processed', controlled_unit_id=unit)
                            for index, unit in enumerate(self.units))
        self.last._quality_native_history = lambda: self.history
        self.workorders = Rows([self.first, self.last])
        for workorder in self.workorders:
            workorder.quality_native_identity_pending = False
        self.run = Row(id=20, state='registered', quantity=3, production_id=p,
                       unit_ids=self.units, workorder_id=Rows([self.last]))
        for unit in self.units:
            unit.registration_run_id = self.run
        p._full_release_units = lambda: self.units
        p._full_release_workorders = lambda: self.workorders
        p._check_named_stock_history = Mock()
        p._validate_quality_batch_controls = Mock()
        p._validate_full_release_lot = Mock()
        p.lot_producing_id = False
        for unit in self.units:
            unit.production_id = p
        self.run.product_id, self.run.company_id = p.product_id, p.company_id

    def validate(self):
        return self.production._validate_quality_actual_batch(self.units, 3)

    def test_three_of_five_uses_real_early_quantity_without_historical_identities(self):
        self.assertTrue(self.validate())
        self.production._validate_quality_batch_controls.assert_called_once_with(
            self.units, self.workorders, standard=True)
        self.assertFalse(self.first._quality_native_history())
        self.assertEqual(self.units.ids, [101, 102, 103])

    def test_revalidation_after_native_split_accepts_same_three(self):
        self.production.product_qty = 3
        self.last.state = 'done'
        self.assertTrue(self.validate())

    def test_required_route_uses_same_protected_actual_batch_release(self):
        self.production.quality_unit_route_required = True
        self.assertTrue(self.validate())
        self.production._validate_quality_batch_controls.assert_called_once()

    def test_historical_transfers_cannot_be_opened(self):
        self.production.quality_transfer_controlled = True
        with self.assertRaises(UserError):
            self.validate()

    def test_foreign_order_product_or_company_is_rejected(self):
        unit = self.units[0]
        for name in ('company_id', 'product_id', '_quality_current_production'):
            original = getattr(unit, name)
            setattr(unit, name, (lambda: object()) if callable(original) else object())
            with self.subTest(field=name), self.assertRaises(UserError):
                self.validate()
            setattr(unit, name, original)

    def test_incomplete_or_duplicate_physical_list_is_rejected(self):
        for units in (self.units[:2], Rows([self.units[0], self.units[0], self.units[2]])):
            with self.assertRaises(UserError):
                self.production._validate_quality_actual_batch(units, 3)

    def test_wrong_current_quantity_is_rejected(self):
        for value in (0, 2, 5, math.nan, math.inf):
            self.production.qty_producing = value
            with self.subTest(quantity=value), self.assertRaises(UserError):
                self.validate()

    def test_non_integer_nonfinite_and_over_plan_quantity_is_rejected(self):
        for value in (-1, 0, 2.5, 6, math.inf, math.nan):
            with self.subTest(quantity=value), self.assertRaises(UserError):
                self.production._validate_quality_actual_batch(self.units, value)

    def test_prior_stock_history_is_not_ignored(self):
        self.production._check_named_stock_history.side_effect = UserError('prior posting')
        with self.assertRaisesRegex(UserError, 'prior posting'):
            self.validate()

    def test_previously_posted_unit_is_rejected(self):
        self.units._assert_not_stock_posted = Mock(side_effect=UserError('posted unit'))
        with self.assertRaisesRegex(UserError, 'posted unit'):
            self.validate()

    def test_missing_or_wrong_registration_is_rejected(self):
        for name, value in (('state', 'draft'), ('quantity', 5), ('production_id', object()),
                            ('unit_ids', self.units[:2]), ('workorder_id', Rows())):
            original = getattr(self.run, name)
            setattr(self.run, name, value)
            with self.subTest(field=name), self.assertRaises(UserError):
                self.validate()
            setattr(self.run, name, original)

    def test_early_operation_must_be_finished_for_exact_actual_quantity(self):
        for name, value in (('state', 'progress'), ('state', 'cancel'), ('qty_produced', 2)):
            original = getattr(self.first, name)
            setattr(self.first, name, value)
            with self.subTest(field=name, value=value), self.assertRaises(UserError):
                self.validate()
            setattr(self.first, name, original)

    def test_completed_early_aggregate_is_not_rewritten_or_backfilled(self):
        self.first.qty_produced = 5
        self.assertTrue(self.validate())
        self.assertEqual(self.first.qty_produced, 5)
        self.assertFalse(self.first._quality_native_history())

    def test_partial_early_physical_execution_is_enough_after_qc(self):
        self.first.state = 'progress'
        self.first._quality_native_history = lambda: self.history
        self.run.workorder_id = Rows([self.first])
        self.assertTrue(self.validate())

    def test_final_operation_requires_exact_processed_identity_set(self):
        original = self.history
        for history in (Rows(), original[:2], Rows([original[0], original[0], original[2]])):
            self.history = history
            with self.assertRaises(UserError):
                self.validate()
        self.history = original
        self.history[0].state = 'progress'
        with self.assertRaises(UserError):
            self.validate()

    def test_active_timer_blocks_release(self):
        self.last.time_ids = Rows([Row(id=1, date_end=False)])
        with self.assertRaises(UserError):
            self.validate()

    def test_stock_destination_must_be_internal_and_in_company(self):
        for name, value in (('usage', 'customer'), ('scrap_location', True), ('company_id', object())):
            original = getattr(self.production.location_dest_id, name)
            setattr(self.production.location_dest_id, name, value)
            with self.assertRaises(UserError):
                self.validate()
            setattr(self.production.location_dest_id, name, original)

    def test_batch_controls_failures_propagate_instead_of_filtering_units(self):
        for reason in ('FAIL', 'Pending', 'missing admission', 'open hold', 'missing required check'):
            self.production._validate_quality_batch_controls.side_effect = UserError(reason)
            with self.subTest(reason=reason), self.assertRaisesRegex(UserError, reason):
                self.validate()

    def test_dependencies_are_linear_and_serial_remains_closed(self):
        self.first._quality_native_predecessors = lambda: Rows()
        self.last._quality_native_predecessors = lambda: Rows()
        self.production.allow_workorder_dependencies = True
        with self.assertRaises(UserError):
            self.validate()
        self.last._quality_native_predecessors = lambda: Rows([self.first])
        self.assertTrue(self.validate())
        self.production.allow_workorder_dependencies = False
        self.production.product_id.tracking = 'serial'
        with self.assertRaisesRegex(UserError, 'серійним'):
            self.validate()


class TestActualBatchInventoryBoundary(unittest.TestCase):
    def _stock_audit(self):
        cls = shipped_class('models/quality_actual_batch_release.py', 'QualityActualBatchProduction',
            {'_check_quality_actual_batch_stock'},
            float_compare=lambda left, right, precision_rounding: 0 if abs(left - right) < 0.001 else 1)
        p = cls()
        p.ensure_one = Mock()
        p.state, p.product_qty, p.qty_produced = 'done', 3, 3
        p.workorder_ids = Rows([Row(id=1, state='done')])
        self.move = Row(id=20, state='done', quantity=6, unit_factor=6,
                        product_uom=NS(rounding=1),
                        move_line_ids=Rows([Row(id=30, location_dest_id=NS(usage='production'))]))
        p.move_raw_ids = Rows([self.move])
        self.audit_production = p
        return lambda: p._check_quality_actual_batch_stock(NS(quantity=3), {20: 6})

    def test_consumption_audit_uses_pre_post_snapshot_not_recomputed_unit_factor(self):
        # Native factor is 6 / ((3 - 3) or 1) = 6 after posting, not 2.
        self._stock_audit()()

    def test_wrong_or_unposted_consumption_rolls_back(self):
        audit = self._stock_audit()
        self.move.quantity = 10
        with self.assertRaises(UserError):
            audit()
        self.move.quantity, self.move.state = 6, 'assigned'
        with self.assertRaises(UserError):
            audit()

    def test_changed_component_set_is_rejected(self):
        audit = self._stock_audit()
        self.audit_production.move_raw_ids = Rows()
        with self.assertRaises(UserError):
            audit()

    def test_native_closure_must_close_all_operations(self):
        audit = self._stock_audit()
        self.audit_production.workorder_ids[0].state = 'progress'
        with self.assertRaises(UserError):
            audit()

    def test_quality_is_revalidated_before_native_inventory(self):
        events = []
        class Parent:
            def __iter__(self):
                return iter([self])
            def _post_inventory(self, cancel_backorder=False):
                events.append(('stock', cancel_backorder))
                return True
        operation = NS(actual_batch=True, unit_ids=(101, 102, 103), quantity=3)
        cls = shipped_class('models/quality_actual_batch_release.py', 'QualityActualBatchProduction',
            {'_post_inventory'}, parent=Parent, native_operation=lambda: operation)
        p = cls()
        class Env:
            cr = object()
            def __getitem__(self, name):
                return NS(browse=lambda ids: ids)
        p.env = Env()
        p.lot_producing_id = NS(id=False)
        operation.finished_lot_id = False
        operation.production = p
        p._validate_quality_actual_batch = lambda units, quantity: events.append(('quality', units, quantity))
        self.assertTrue(p._post_inventory(cancel_backorder=True))
        self.assertEqual(events, [('quality', (101, 102, 103), 3), ('stock', True)])
        events.clear()
        p._validate_quality_actual_batch = Mock(side_effect=UserError('hold'))
        with self.assertRaises(UserError):
            p._post_inventory(cancel_backorder=True)
        self.assertFalse(events)
        p._validate_quality_actual_batch.side_effect = None
        p.lot_producing_id.id = 55
        with self.assertRaisesRegex(UserError, 'змінилася партія'):
            p._post_inventory(cancel_backorder=True)
        self.assertFalse(events)

    def test_new_entry_has_no_stock_algorithm_or_rpc_unlock(self):
        import ast
        tree = ast.parse((ROOT / 'models/quality_actual_batch_release.py').read_text(encoding='utf-8'))
        calls = [node.func for node in ast.walk(tree) if isinstance(node, ast.Call)]
        attributes = {node.attr for node in calls if isinstance(node, ast.Attribute)}
        self.assertFalse(attributes & {'sudo', 'execute', '_action_done', '_cal_price', '_cal_cost'})
        names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        self.assertNotIn('button_mark_done', names)
        self.assertIn('_native_quality_complete', attributes)
        self.assertIn('_record_standard_stock_facts', attributes)


if __name__ == '__main__':
    unittest.main()
