"""Real stock/return integration; QC gates are isolated in test_incoming_quality."""
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

from odoo import api, Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.service.model import retrying


@tagged('post_install', '-at_install', 'incoming_stage_a')
class IncomingStockFixture(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref('stock.group_stock_manager') | cls.env.ref('quality.group_quality_user')
        cls.warehouse = cls.env['stock.warehouse'].create({'name': 'A Incoming CAB', 'code': 'CAB'})
        cls.warehouse.action_enable_incoming_quality()
        cls.vendor = cls.env['res.partner'].create({'name': 'A Incoming Supplier'})
        cls.product = cls.env['product.product'].create({'name': 'A Incoming Product', 'type': 'product',
                                                        'quality_incoming_controlled': True})
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')

    def _receipt(self, quantity=10, product=None, warehouse=None, received=None, lot=None, owner=None, package=None):
        product = product or self.product
        warehouse = warehouse or self.warehouse
        picking = self.env['stock.picking'].create({
            'picking_type_id': warehouse.in_type_id.id, 'partner_id': self.vendor.id,
            'location_id': self.supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
        })
        move = self.env['stock.move'].create({
            'name': product.name, 'picking_id': picking.id, 'product_id': product.id,
            'product_uom': product.uom_id.id, 'product_uom_qty': quantity,
            'location_id': self.supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
        })
        picking.action_confirm()
        move.move_line_ids.unlink()
        self.env['stock.move.line'].create({
            'move_id': move.id, 'picking_id': picking.id, 'product_id': product.id, 'product_uom_id': product.uom_id.id,
            'location_id': self.supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'quantity': quantity if received is None else received, 'lot_id': lot.id if lot else False,
            'owner_id': owner.id if owner else False, 'result_package_id': package.id if package else False,
        })
        move.picked = True
        picking._action_done()
        return picking, picking.quality_incoming_hold_ids

    def _quantity(self, location, product=None, lot=None):
        return self.env['stock.quant']._get_available_quantity(product or self.product, location,
                                                              lot_id=lot, strict=True)

    def _gate(self, hold, name, value):
        return patch.object(type(hold), name, lambda records: value)


@tagged('post_install', '-at_install', 'incoming_stage_a')
class TestIncomingStock(IncomingStockFixture):

    def test_receipt_is_quarantined_and_not_reservable(self):
        _picking, hold = self._receipt()
        self.assertEqual(hold.received_qty, 10)
        self.assertEqual(self._quantity(hold.location_id), 10)
        self.assertEqual(self._quantity(self.warehouse.lot_stock_id), 0)
        quant = self.env['stock.quant']
        self.assertFalse(quant._get_reserve_quantity(self.product, hold.location_id, 10))
        self.assertFalse(quant._get_reserve_quantity(self.product, self.warehouse.view_location_id, 10))
        with self.assertRaises(UserError):
            hold._execute('release', 'unchecked', 10, 'No QC', 'pending')

    def test_full_pass_release_and_repeat(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_sampling_passed', True):
            first = hold._execute('release', 'unchecked', 10, 'All samples PASS', 'pass')
            second = hold._execute('release', 'unchecked', 10, 'All samples PASS', 'pass')
        self.assertEqual(first, second)
        self.assertEqual(first.move_id.state, 'done')
        self.assertEqual(hold.remaining_qty, 0)
        self.assertEqual(self._quantity(self.warehouse.lot_stock_id), 10)
        self.assertEqual(len(hold.operation_ids), 1)

    def test_quick_partial_return_without_full_inspection(self):
        picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            operation = hold._execute('return', 'unchecked', 4, 'Sampling defect', 'return4')
            self.assertEqual(operation, hold._execute('return', 'unchecked', 4, 'Sampling defect', 'return4'))
        self.assertFalse(hold.inspection_ids)
        self.assertEqual(operation.picking_id.return_id, picking)
        self.assertEqual(operation.picking_id.location_id, hold.location_id)
        self.assertEqual(operation.picking_id.location_dest_id, self.supplier_location)
        self.assertEqual(operation.move_id.origin_returned_move_id, hold.receipt_move_id)
        self.assertEqual(operation.move_id.location_id, hold.location_id)
        self.assertEqual(operation.move_id.location_dest_id, self.supplier_location)
        self.assertEqual((hold.returned_qty, hold.remaining_qty, hold.unchecked_qty), (4, 6, 6))
        self.assertEqual(self._quantity(hold.location_id), 6)

    def test_full_control_separates_good_bad_and_return(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            line = hold._inspect(7, 3, '100% inspected', 'inspection')
            self.assertEqual(line, hold._inspect(7, 3, '100% inspected', 'inspection'))
            good = hold._execute('release', 'good', 7, 'Confirmed good units', 'good7')
            bad = hold._execute('return', 'bad', 2, 'Defective units', 'bad2')
        self.assertEqual(good.move_id.quantity, 7)
        self.assertEqual(bad.move_id.quantity, 2)
        self.assertEqual((hold.good_qty, hold.bad_qty, hold.unchecked_qty), (0, 1, 0))
        self.assertEqual((hold.released_qty, hold.returned_qty, hold.remaining_qty), (7, 2, 1))
        self.assertEqual(self._quantity(hold.location_id), 1)

    def test_full_fail_cannot_release(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            hold._inspect(0, 10, 'All defective', 'allbad')
        with self.assertRaises(UserError):
            hold._execute('release', 'bad', 10, 'Not allowed', 'bad')
        self.assertEqual(hold.remaining_qty, 10)

    def test_manual_defect_without_plan_allows_quick_return(self):
        _picking, hold = self._receipt()
        hold._report_failure('Damaged goods')
        hold._report_failure('Damaged goods')
        hold._execute('return', 'unchecked', 3, 'Visible damage', 'manual')
        self.assertEqual(hold.returned_qty, 3)
        self.assertFalse(hold.inspection_ids)

    def test_owner_and_complete_package_are_preserved(self):
        package = self.env['stock.quant.package'].create({'name': 'A Incoming Package'})
        _picking, hold = self._receipt(owner=self.vendor, package=package)
        with self._gate(hold, '_sampling_passed', True):
            with self.assertRaises(UserError):
                hold._execute('release', 'unchecked', 3, 'Partial package denied', 'package3')
            operation = hold._execute('release', 'unchecked', 10, 'Whole inspected package', 'package10')
        line = operation.move_id.move_line_ids
        self.assertEqual(line.owner_id, self.vendor)
        self.assertEqual(line.package_id, package)
        self.assertEqual(line.result_package_id, package)

    def test_other_company_user_cannot_read_or_execute_hold(self):
        _picking, hold = self._receipt()
        company = self.env['res.company'].create({'name': 'A Different Company'})
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'A Other Company', 'login': 'a-incoming-other-company',
            'company_id': company.id, 'company_ids': [Command.set(company.ids)],
            'groups_id': [Command.set(self.env.ref('stock.group_stock_user').ids)],
        })
        with self.assertRaises(AccessError):
            hold.with_user(user).with_context(allowed_company_ids=company.ids)._execute(
                'return', 'unchecked', 1, 'Wrong company', 'crosscompany')

    def test_partial_inspection_and_return_keep_unchecked_balance(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            hold._inspect(4, 2, 'Six inspected', 'six')
            hold._execute('return', 'unchecked', 3, 'Return uninspected remainder', 'unseen3')
            with self.assertRaises(ValidationError):
                hold._inspect(2, 0, 'Cannot inspect returned units', 'over')
        self.assertEqual((hold.good_qty, hold.bad_qty, hold.unchecked_qty, hold.remaining_qty), (4, 2, 1, 7))

    def test_quantities_cannot_exceed_source_or_be_fractional_pieces(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            for good, bad in [(11, 0), (-1, 2), (float('nan'), 0), (7.4, 2.6)]:
                with self.assertRaises(ValidationError):
                    hold._inspect(good, bad, 'Invalid', str(good))
            with self.assertRaises(ValidationError):
                hold._execute('return', 'unchecked', 11, 'Too much', 'over')
        self.assertEqual(hold.remaining_qty, 10)

    def test_same_lot_two_receipts_are_accounted_independently(self):
        product = self.env['product.product'].create({'name': 'A Lot Product', 'type': 'product', 'tracking': 'lot',
                                                     'quality_incoming_controlled': True})
        lot = self.env['stock.lot'].create({'name': 'A Incoming Same Lot', 'product_id': product.id})
        picking1, hold1 = self._receipt(product=product, lot=lot)
        picking2, hold2 = self._receipt(quantity=3, product=product, lot=lot)
        with self._gate(hold1, '_has_failure', True):
            hold1._execute('return', 'unchecked', 10, 'First receipt only', 'first')
            with self.assertRaises(ValidationError):
                hold1._execute('return', 'unchecked', 1, 'Cannot use second receipt', 'steal')
        self.assertNotEqual(picking1, picking2)
        self.assertEqual(hold2.remaining_qty, 3)
        self.assertEqual(self._quantity(hold2.location_id, product, lot), 3)

    def test_serial_return_preserves_identity(self):
        product = self.env['product.product'].create({'name': 'A Serial Product', 'type': 'product', 'tracking': 'serial',
                                                     'quality_incoming_controlled': True})
        lot = self.env['stock.lot'].create({'name': 'A-SERIAL-1', 'product_id': product.id})
        _picking, hold = self._receipt(quantity=1, product=product, lot=lot)
        with self._gate(hold, '_has_failure', True):
            operation = hold._execute('return', 'unchecked', 1, 'Serial defect', 'serial')
        self.assertEqual(operation.move_id.move_line_ids.lot_id, lot)

    def test_partial_receipt_backorder_is_received_to_quarantine_separately(self):
        picking, hold = self._receipt(quantity=10, received=4)
        self.assertEqual(hold.received_qty, 4)
        backorder = self.env['stock.picking'].search([('backorder_id', '=', picking.id)])
        self.assertEqual(backorder.move_ids.location_dest_id, self.warehouse.lot_stock_id)
        backorder.move_ids.quantity = 6
        backorder.move_ids.picked = True
        backorder._action_done()
        self.assertEqual(backorder.quality_incoming_hold_ids.received_qty, 6)
        self.assertEqual(hold.remaining_qty, 4)

    def test_other_warehouse_and_preexisting_receipts_are_unchanged(self):
        warehouse = self.env['stock.warehouse'].create({'name': 'A Uncontrolled Warehouse', 'code': 'AOTH'})
        _picking, holds = self._receipt(warehouse=warehouse)
        self.assertFalse(holds)
        self.assertEqual(self._quantity(warehouse.lot_stock_id), 10)

    def test_old_draft_receipt_is_not_migrated_when_enabling_cab(self):
        company = self.env['res.company'].create({'name': 'A Existing Receipts'})
        env = self.env(context=dict(self.env.context, allowed_company_ids=[company.id]))
        warehouse = env['stock.warehouse'].create({'name': 'A Old CAB', 'code': 'CAB', 'company_id': company.id})
        picking = env['stock.picking'].create({
            'picking_type_id': warehouse.in_type_id.id, 'company_id': company.id,
            'location_id': self.supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id,
            'move_ids': [Command.create({'name': 'Old receipt', 'product_id': self.product.id,
                'product_uom': self.product.uom_id.id, 'product_uom_qty': 2, 'company_id': company.id,
                'location_id': self.supplier_location.id, 'location_dest_id': warehouse.lot_stock_id.id})],
        })
        warehouse.action_enable_incoming_quality()
        picking.action_confirm()
        picking.move_ids.quantity = 2
        picking.move_ids.picked = True
        picking._action_done()
        self.assertFalse(picking.quality_incoming_hold_ids)
        self.assertEqual(picking.move_ids.location_dest_id, warehouse.lot_stock_id)

    def test_only_explicitly_controlled_products_are_opted_in(self):
        product = self.env['product.product'].create({'name': 'A Not Controlled', 'type': 'product'})
        _picking, holds = self._receipt(product=product)
        self.assertFalse(holds)

    def test_overreceipt_accounts_all_posted_source_lines(self):
        _picking, holds = self._receipt(quantity=5, received=8)
        self.assertEqual(sum(holds.mapped('received_qty')), 8)
        self.assertEqual(self._quantity(self.warehouse.quality_incoming_location_id), 8)

    def test_manual_context_and_negative_stock_do_not_bypass_quarantine(self):
        _picking, hold = self._receipt()
        quant = self.env['stock.quant'].with_context(quality_vataga_quarantine=True,
                                                  quality_vataga_incoming_operation=True)
        with self.assertRaises(UserError):
            quant._update_available_quantity(self.product, hold.location_id, -1)
        with self.assertRaises(UserError):
            quant._update_available_quantity(self.product, self.warehouse.lot_stock_id, -1)
        with self.assertRaises(AccessError):
            hold.write({'received_qty': 100})
        with self.assertRaises(AccessError):
            hold.receipt_move_id.write({'quality_incoming_required': False})

    def test_stock_user_cannot_confirm_inspection_or_release(self):
        _picking, hold = self._receipt()
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'A Warehouse Operator', 'login': 'a-incoming-stock',
            'groups_id': [Command.set([self.env.ref('stock.group_stock_user').id])],
        })
        with self.assertRaises(AccessError):
            hold.with_user(user)._inspect(10, 0, 'No quality rights', 'norights')
        with self.assertRaises(AccessError):
            hold.with_user(user)._execute('release', 'unchecked', 10, 'No quality rights', 'norights')

    def test_failed_execution_rolls_back_stock_and_ledger(self):
        _picking, hold = self._receipt()
        with self._gate(hold, '_has_failure', True):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                with patch.object(type(hold), '_return_supplier', side_effect=UserError('Simulated failure')):
                    hold._execute('return', 'unchecked', 5, 'Failed request', 'failed')
        self.assertFalse(hold.operation_ids)
        self.assertEqual(self._quantity(hold.location_id), 10)


@tagged('post_install', '-at_install', 'incoming_stage_a')
class TestIncomingConcurrency(TransactionCase):
    """Separate committed fixtures and real cursors, never a shared test cursor.

    The fixtures belong to a new company in the disposable test database and
    deliberately survive this test's transaction so both clients can see them.
    Only the QC decision is stubbed; all stock/ledger/SQL paths are production.
    """

    def _committed_hold(self):
        with self.registry.cursor() as cr:
            root = api.Environment(cr, 1, {})
            label = uuid4().hex
            company = root['res.company'].create({'name': 'A Concurrent ' + label})
            groups = [root.ref('stock.group_stock_manager').id, root.ref('quality.group_quality_user').id]
            users = root['res.users'].with_context(no_reset_password=True).create([{
                'name': 'A Concurrent Inspector ' + str(i), 'login': label + str(i),
                'email': label + str(i) + '@example.invalid',
                'company_id': company.id, 'company_ids': [Command.set(company.ids)],
                'groups_id': [Command.set(groups)],
            } for i in range(2)])
            env = api.Environment(cr, users[0].id, {'allowed_company_ids': [company.id]})
            warehouse = root['stock.warehouse'].with_company(company).create({'name': 'A Concurrent CAB', 'code': 'CAB'})
            warehouse = warehouse.with_env(env)
            warehouse.action_enable_incoming_quality()
            product = env['product.product'].create({'name': 'A Concurrent Product', 'type': 'product',
                                                     'quality_incoming_controlled': True})
            vendor = env['res.partner'].create({'name': 'A Concurrent Supplier'})
            fixture = SimpleNamespace(env=env, warehouse=warehouse, product=product, vendor=vendor,
                                      supplier_location=env.ref('stock.stock_location_suppliers'))
            _picking, hold = IncomingStockFixture._receipt(fixture)
            result = hold.id, users.ids, company.id
            cr.commit()
            return result

    def _race(self, inspect=False):
        hold_id, users, company_id = self._committed_hold()
        barrier = Barrier(2)

        def worker(user_id):
            with self.registry.cursor() as cr:
                env = api.Environment(cr, user_id, {'allowed_company_ids': [company_id]})
                hold = env['quality.incoming.hold'].browse(hold_id)
                hold.read(['received_qty'])  # both clients start with the old snapshot
                barrier.wait(timeout=15)
                try:
                    if inspect:
                        result = retrying(lambda: hold._inspect(6, 0, 'Concurrent inspection', str(user_id)), env)
                    else:
                        result = retrying(lambda: hold._execute('return', 'unchecked', 10,
                                                               'Concurrent confirmed return', 'same-request'), env)
                    return ('done', result.id)
                except ValidationError:
                    cr.rollback()
                    return ('rejected', None)

        with patch.object(type(self.env['quality.incoming.hold']), '_has_failure', lambda records: True):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(worker, user_id) for user_id in users]
                results = [future.result(timeout=45) for future in futures]
        with self.registry.cursor() as cr:
            env = api.Environment(cr, 1, {})
            hold = env['quality.incoming.hold'].browse(hold_id)
            if inspect:
                self.assertEqual(sorted(result[0] for result in results), ['done', 'rejected'])
                self.assertEqual((hold.checked_qty, hold.unchecked_qty), (6, 4))
            else:
                self.assertEqual(results[0], results[1])
                self.assertEqual(len(hold.operation_ids), 1)
                self.assertEqual((hold.returned_qty, hold.remaining_qty), (10, 0))

    def test_two_users_cannot_double_count_inspected_quantities(self):
        self._race(inspect=True)

    def test_two_users_confirming_same_return_create_one_stock_document(self):
        self._race()
