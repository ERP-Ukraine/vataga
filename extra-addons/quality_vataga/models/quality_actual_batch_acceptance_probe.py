"""Administrator-only actual-batch acceptance, with no durable fixture writes.

Like quality_partial_release_probe, each scenario exits a savepoint by a private
exception. A separate cursor is also always rolled back/closed: mail/bus
post-commit callbacks cannot escape into the Scheduled Action transaction.
No test classes, global patches, SQL mutations, business bypass or commits.
"""
import threading
import uuid

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .quality_partial_release_probe import _ProbeRollback, _assert
from .quality_native_workorder import PARTIAL_WO_MESSAGE


ALLOWED_DATABASE = 'odoo17-vataga-staging'
SCENARIOS = ('P1', 'N1', 'N2', 'N3', 'N4', 'N5', 'N6', 'N7', 'N8', 'N12', 'N14')
ORM_ONLY = {
    'N9': 'Backorder fault injection requires an isolated Enterprise test process.',
    'N10': 'Stock fault injection requires an isolated Enterprise test process.',
    'N11': 'Ordinary production regression belongs to the isolated ORM suite.',
    'N13': 'Repeated-call ORM regression exists; true concurrency needs independent transactions on a disposable committed fixture and is NOT RUN.',
}
# Counts/high-water marks are read in one repeatable-read transaction, so live
# users cannot cause false cleanup failures by creating unrelated records.
AUDIT_MODELS = (
    'product.category', 'product.template', 'product.product', 'ir.property',
    'stock.location', 'stock.picking.type', 'ir.sequence', 'ir.sequence.date_range',
    'mrp.workcenter', 'mrp.bom', 'mrp.bom.line', 'mrp.routing.workcenter',
    'procurement.group', 'mrp.production', 'mrp.workorder',
    'mrp.workcenter.productivity', 'resource.calendar.leaves',
    'quality.alert.team', 'quality.point', 'quality.check', 'quality.check.sample',
    'quality.check.measurement.column', 'quality.check.measurement.value',
    'quality.check.equipment.selection', 'quality.production.run', 'quality.production.unit',
    'quality.unit.admission', 'quality.unit.execution.batch', 'quality.unit.execution',
    'quality.unit.stock.fact', 'quality.unit.continuation', 'quality.alert',
    'stock.picking', 'stock.move', 'stock.move.line', 'stock.quant', 'stock.valuation.layer',
    'quality.unit.transfer', 'quality.unit.transfer.event',
    'mail.message', 'mail.mail', 'mail.notification', 'mail.followers', 'mail.activity',
)


class _ProbeSkip(Exception):
    """Only an explicitly detected prerequisite/safety limitation is a SKIP."""


def _guard(env, expected_database):
    if (expected_database != ALLOWED_DATABASE or env.cr.dbname != ALLOWED_DATABASE
            or not env.user.has_group('base.group_system')):
        raise AccessError(_('Діагностику дозволено лише системному адміністратору в явно визначеній тестовій базі з точною назвою.'))


def _expected_denial(call, expected_messages):
    """Never pass because of an ACL, setup error or an arbitrary exception."""
    try:
        call()
    except AccessError:
        raise
    except UserError as error:
        _assert(str(error) in expected_messages,
                'Wrong business rejection (%s): %s' % (type(error).__name__, error))
        return {'error_type': type(error).__name__, 'reason': str(error)}
    raise ValidationError('Invalid release was accepted')


def _overall(results):
    if any(row['status'] == 'FAIL' for row in results):
        return 'FAIL'
    if any(row['status'] in ('SKIP', 'NOT RUN') for row in results):
        return 'INCOMPLETE'
    return 'PASS'


class QualityActualBatchAcceptanceProbe(models.Model):
    _inherit = 'mrp.production'

    @api.model
    def _run_quality_actual_batch_acceptance_probe(self, expected_database):
        _guard(self.env, expected_database)  # Before opening any cursor/fixture.
        results = [self._quality_actual_probe_isolated_case(code) for code in SCENARIOS]
        orm = [{'code': code, 'status': 'NOT RUN', 'reason': reason}
               for code, reason in ORM_ONLY.items()]
        module = self.env['ir.module.module'].search([('name', '=', 'quality_vataga')], limit=1)
        return {
            'title': 'ACTUAL BATCH RELEASE — ENTERPRISE ACCEPTANCE',
            'database': self.env.cr.dbname, 'module': 'quality_vataga',
            'installed_version': module.installed_version,
            'scenarios': results, 'enterprise_orm': orm,
            'rollback': 'COMPLETE' if all(row['rollback'] == 'COMPLETE' for row in results) else 'UNVERIFIED',
            'persistent_test_records': 0 if all(row['rollback'] == 'COMPLETE' for row in results) else None,
            'scheduled_scenarios': _overall(results), 'overall': _overall(results + orm),
            'limitations': [
                'PostgreSQL/document sequence gaps may remain; nextval is not transactional.',
                'This report does not run or claim Enterprise ORM fault/concurrency tests.',
            ],
        }

    @api.model
    def _quality_actual_probe_isolated_case(self, code):
        result = {'code': code, 'status': 'FAIL', 'stage': 'open_transaction',
                  'rollback': 'UNVERIFIED', 'release_action_attempted': False}
        cr = None
        try:
            cr = self.env.registry.cursor()  # Never use its committing context manager.
            env = api.Environment(cr, self.env.uid, {
                'allowed_company_ids': self.env.companies.ids, 'lang': self.env.lang,
                'tz': self.env.user.tz, 'tracking_disable': True, 'mail_notrack': True,
                'mail_create_nosubscribe': True, 'mail_create_nolog': True,
                'mail_notify_force_send': False,
            }, su=False)
            _guard(env, ALLOWED_DATABASE)
            probe = env['mrp.production']
            before = None
            try:
                with cr.savepoint():
                    try:
                        result['stage'] = 'preconditions'
                        probe._quality_actual_probe_preconditions()
                        result['stage'] = 'rollback_baseline'
                        before = {name: (env[name].search_count([]), env[name].search([], order='id desc', limit=1).id or 0)
                                  for name in AUDIT_MODELS}
                        result.update(probe._quality_actual_probe_case(code, result))
                        env.flush_all()
                    except _ProbeSkip as error:
                        result.update(status='SKIP', reason=str(error), error_type=type(error).__name__)
                    except Exception as error:
                        result.update(status='FAIL', reason=str(error), error_type=type(error).__name__)
                    # Unconditional exceptional exit even after a PASS/SKIP/FAIL.
                    raise _ProbeRollback()
            except _ProbeRollback:
                env.invalidate_all(flush=False)
            result['verification_stage'] = 'after_savepoint_rollback'
            if before is not None:
                residues = {name: env[name].search_count([('id', '>', maximum)])
                            for name, (_count, maximum) in before.items()}
                counts = {name: env[name].search_count([]) for name in before}
                _assert(not any(residues.values()) and all(counts[name] == before[name][0] for name in before),
                        'Rollback verification failed: %s' % residues)
            else:
                # Preconditions/baseline are read only; fixture creation has
                # not been entered, including a SKIP for missing normal roles.
                result['no_fixture_created'] = True
            result.update(rollback='COMPLETE', persistent_test_records=0)
        except Exception as error:
            result.update(status='FAIL', reason=str(error), error_type=type(error).__name__)
        finally:
            # Discard callbacks as well as SQL, including unexpected setup,
            # serialization, assertion and cleanup errors. Never touch caller cr.
            if cr is not None:
                try:
                    cr.rollback()
                finally:
                    cr.close()
        return result

    @api.model
    def _quality_actual_probe_preconditions(self):
        groups = ('mrp.group_mrp_user', 'stock.group_stock_user', 'quality.group_quality_manager')
        if not all(self.env.user.has_group(group) for group in groups):
            raise _ProbeSkip('Administrator also needs normal Manufacturing, Stock and Quality Manager roles.')
        if 'workorder_id' not in self.env['quality.check']._fields:
            raise _ProbeSkip('Enterprise quality/workorder integration is unavailable.')
        if getattr(threading.current_thread(), 'testing', False):
            raise _ProbeSkip('Scheduled Action probe requires normal mail post-commit mode, not a test-mode thread.')
        # Active custom automations could perform synchronous external effects.
        # Read only; never disable them or silently treat their failures as PASS.
        if 'base.automation' in self.env:
            automations = self.env['base.automation'].search([
                ('active', '=', True), ('model_id.model', 'in', list(AUDIT_MODELS))])
            if automations:
                raise _ProbeSkip('Active automations on fixture models need isolated-process review: %s' % automations.ids)

    @api.model
    def _quality_actual_probe_setup(self, outcome='pass', unfinished_first=False, progress=None):
        """Own products, locations, operation type and QCP; existing warehouse is read only."""
        warehouse = self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id), ('manufacture_steps', '=', 'mrp_one_step')], limit=1)
        if not warehouse:
            raise _ProbeSkip('No one-step manufacturing warehouse in the current company; settings remain unchanged.')
        test_type = self.env['quality.point.test_type'].search([('technical_name', '=', 'passfail')], limit=1)
        Point = self.env['quality.point']
        operation_fields = [(name, field) for name, field in Point._fields.items()
                            if getattr(field, 'comodel_name', None) == 'mrp.routing.workcenter'
                            and field.type in ('many2one', 'many2many') and not field.compute]
        if not test_type or not operation_fields or 'product_ids' not in Point._fields:
            raise _ProbeSkip('Missing native pass/fail test type or exact operation/product QCP assignment.')
        tag = 'QAB-' + uuid.uuid4().hex[:16]
        unit = self.env.ref('uom.product_uom_unit')
        category = self.env['product.category'].create({
            'name': tag, 'property_cost_method': 'fifo', 'property_valuation': 'manual_periodic'})
        products = self.env['product.product'].create([
            {'name': tag + suffix, 'type': 'product', 'tracking': 'none', 'categ_id': category.id,
             'company_id': self.env.company.id, 'uom_id': unit.id, 'uom_po_id': unit.id,
             'standard_price': price} for suffix, price in (('-finished', 0), ('-component', 10))])
        product, component = products
        location = self.env['stock.location'].create({
            'name': tag, 'usage': 'internal', 'location_id': warehouse.lot_stock_id.id,
            'company_id': self.env.company.id})
        picking_type = self.env['stock.picking.type'].create({
            'name': tag, 'code': 'mrp_operation', 'sequence_code': tag,
            'warehouse_id': warehouse.id, 'company_id': self.env.company.id,
            'default_location_src_id': location.id, 'default_location_dest_id': location.id,
            'create_backorder': 'ask', 'reservation_method': 'at_confirm'})
        centers = self.env['mrp.workcenter'].create([
            {'name': tag + '-cut', 'costs_hour': 0}, {'name': tag + '-inspect', 'costs_hour': 0}])
        bom = self.env['mrp.bom'].create({
            'code': tag, 'product_tmpl_id': product.product_tmpl_id.id, 'product_qty': 1,
            'product_uom_id': unit.id, 'company_id': self.env.company.id,
            'consumption': 'flexible',
            'bom_line_ids': [(0, 0, {'product_id': component.id, 'product_qty': 2, 'product_uom_id': unit.id})],
            'operation_ids': [(0, 0, {'name': tag + '-operation-%s' % index,
                                    'sequence': index * 10, 'workcenter_id': center.id})
                              for index, center in enumerate(centers, 1)]})
        team = self.env['quality.alert.team'].create({'name': tag})
        final_operation = bom.operation_ids.sorted('sequence')[-1:]
        name, field = operation_fields[0]
        values = {'title': tag, 'team_id': team.id, 'test_type_id': test_type.id,
                  'x_control_stage': 'in_process', 'visual_sample_control_required': True,
                  'quality_unit_route_required': False, 'picking_type_ids': [(6, 0, picking_type.ids)],
                  'product_ids': [(6, 0, product.ids)],
                  name: final_operation.id if field.type == 'many2one' else [(6, 0, final_operation.ids)]}
        if 'company_id' in Point._fields:
            values['company_id'] = self.env.company.id
        point = Point.create(values)
        receipt = self.env['stock.move'].create({
            'name': tag, 'product_id': component.id, 'product_uom': unit.id,
            'product_uom_qty': 20, 'price_unit': 10, 'company_id': self.env.company.id,
            'location_id': self.env.ref('stock.stock_location_suppliers').id, 'location_dest_id': location.id})
        receipt._action_confirm(merge=False)
        receipt.write({'quantity': 20, 'picked': True})
        receipt._action_done()
        scene = {'product': product, 'component': component, 'location': location,
                 'receipt': receipt, 'bom': bom, 'centers': centers, 'team': team,
                 'point': point, 'tag': tag}
        if progress is None:
            progress = {}
        progress['inventory'] = {'after_receipt': self._quality_actual_probe_inventory(scene)}
        _assert(progress['inventory']['after_receipt']['component']['on_hand'] == 20,
                'Test receipt did not put twenty physical components into the fixture location')
        production = self.create({
            'product_id': product.id, 'product_qty': 5, 'product_uom_id': unit.id, 'bom_id': bom.id,
            'company_id': self.env.company.id, 'picking_type_id': picking_type.id,
            'location_src_id': location.id, 'location_dest_id': location.id, 'user_id': False,
            'allow_workorder_dependencies': False})
        production.action_confirm()
        production.action_assign()
        production.qty_producing = 3
        production._set_qty_producing()
        orders = production.workorder_ids.sorted(key=lambda row: (row.operation_id.sequence, row.id))
        _assert(len(orders) == 2, 'Fixture needs exactly two real workorders')
        first, last = orders
        scene.update(production=production, orders=orders)
        # N5 identifies units at the final operation BEFORE any execution.
        # The first operation then records its real three-unit partial work,
        # leaving state=progress. No confirmed route/history is edited later.
        check = self._quality_actual_probe_initialize_check(scene) if unfinished_first else None
        if unfinished_first:
            first.write({'quality_native_unit_ids': [(6, 0, check.sample_ids.controlled_unit_id.ids)]})
        first.button_start()
        if unfinished_first:
            self._quality_partial_probe_timer(first)
        first.button_finish()
        last.button_start(bypass=True)
        # Reuse the existing fixture's native timer fallback, never fake state
        # or quantities. Report that this is not a Shop Floor timer UI test.
        timer_fallback = not last.time_ids.filtered(lambda row: row.user_id == self.env.user and not row.date_end)
        self._quality_partial_probe_timer(last)
        check = check or self._quality_actual_probe_initialize_check(scene)
        samples = check.sample_ids.sorted('sample_number')
        for sample in samples:
            if outcome != 'pending' or sample != samples[-1:]:
                check.update_measurement_visual_result(sample.id, 'yes')
        if outcome == 'fail':
            check.update_measurement_visual_result(samples[-1:].id, 'no')
            check.do_fail()
        elif outcome == 'pass':
            check.do_pass()
        last.button_finish()
        units = production._full_release_units()
        _assert(len(units) == 3 and first.qty_produced == 3
                and first.state == ('progress' if unfinished_first else 'done')
                and last.qty_produced == 3 and len(last._quality_native_history()) == 3,
                'Fixture did not confirm exact native processing for three units')
        if unfinished_first:
            _assert(set(first._quality_native_history().controlled_unit_id.ids) == set(units.ids),
                    'Unfinished first operation lacks its real per-unit processing evidence')
        else:
            _assert(not first._quality_native_history(), 'Fixture fabricated early unit history')
        _assert(not production.quality_transfer_controlled and not production.quality_unit_route_required,
                'Fixture unexpectedly enabled a protected route')
        _assert(not orders.time_ids.filtered(lambda row: not row.date_end), 'Fixture left open work intervals')
        _assert(not production.message_partner_ids, 'Fixture acquired message recipients')
        scene.update(check=check, units=units, samples=samples, synthetic_timer_preparation=timer_fallback)
        progress['inventory']['before_release'] = self._quality_actual_probe_inventory(scene)
        return scene

    @api.model
    def _quality_actual_probe_initialize_check(self, scene):
        production, point = scene['production'], scene['point']
        last = scene['orders'][-1:]
        checks = self.env['quality.check'].search([
            ('point_id', '=', point.id), ('production_id', '=', production.id), ('workorder_id', '=', last.id)])
        _assert(len(checks) <= 1, 'Unexpected duplicate native checks; fixture is not unambiguous')
        check = checks or self.env['quality.check'].create({
            'point_id': point.id, 'team_id': scene['team'].id, 'test_type_id': point.test_type_id.id,
            'product_id': scene['product'].id, 'production_id': production.id, 'workorder_id': last.id,
            'measure_on': 'product'})
        check._initialize_measurement_snapshot()
        check.initialize_in_process_measurement_matrix()
        _assert(len(check.sample_ids) == 3, 'Fixture did not generate exactly three actual samples')
        check.initialize_qc_unit_identity()
        return check

    @api.model
    def _quality_actual_probe_inventory(self, scene):
        """Read physical stock and free stock separately, retaining all evidence."""
        self.env.flush_all()
        Quant = self.env['stock.quant']
        Quant.invalidate_model(['quantity', 'reserved_quantity'])
        products = scene['product'] | scene['component']
        quants = Quant.search([('product_id', 'in', products.ids)], order='id')
        production = scene.get('production', self.env['mrp.production'])
        children = self.search([('quality_native_parent_id', '=', production.id)]) if production else self.browse()
        moves = (scene['receipt'] | production.move_raw_ids | production.move_finished_ids
                 | children.move_raw_ids).sorted('id')
        report = {
            'location_id': scene['location'].id, 'company_id': self.env.company.id,
            'production_id': production.id, 'backorder_ids': children.ids,
            'receipt_move_ids': scene['receipt'].ids, 'move_ids': moves.ids,
            'moves': moves.read(['product_id', 'company_id', 'state', 'quantity', 'product_uom_qty',
                                 'location_id', 'location_dest_id', 'raw_material_production_id', 'production_id']),
            'move_lines': moves.move_line_ids.sorted('id').read([
                'move_id', 'product_id', 'company_id', 'state', 'quantity', 'product_uom_id',
                'picked', 'location_id', 'location_dest_id']),
            'quants': quants.read(['product_id', 'company_id', 'location_id', 'quantity',
                                  'reserved_quantity', 'lot_id', 'package_id', 'owner_id']),
        }
        for label in ('product', 'component'):
            product = scene[label]
            exact = quants.filtered(lambda row: row.product_id == product and row.location_id == scene['location']
                                    and row.company_id == self.env.company)
            report[label] = {
                'product_id': product.id, 'on_hand': sum(exact.mapped('quantity')),
                'reserved': sum(exact.mapped('reserved_quantity')),
                'available': Quant._get_available_quantity(product, scene['location'], strict=True, allow_negative=True),
            }
        child_lines = children.move_raw_ids.filtered(lambda row: row.state not in ('done', 'cancel') and not row.picked).move_line_ids
        report['backorder_component_reserved'] = sum(
            line.product_uom_id._compute_quantity(line.quantity, scene['component'].uom_id)
            for line in child_lines if not line.picked and line.product_id == scene['component']
            and line.location_id == scene['location'])
        return report

    @api.model
    def _quality_actual_probe_assert_inventory(self, inventory):
        # Fixed fixture: receipt 20 - actual consumption 3*2 = physical 14.
        # Its at-confirm backorder for 2 reserves 2*2 = 4, leaving 10 free.
        inventory['expected'] = {
            'product': {'on_hand': 3, 'reserved': 0, 'available': 3},
            'component': {'on_hand': 14, 'reserved': 4, 'available': 10},
            'backorder_component_reserved': 4,
        }
        for label in ('product', 'component'):
            _assert(all(inventory[label][key] == value for key, value in inventory['expected'][label].items()),
                    'Inventory mismatch for %s: actual=%s expected=%s' % (
                        label, inventory[label], inventory['expected'][label]))
        _assert(inventory['backorder_component_reserved'] == 4,
                'Component quant reservation does not match the real backorder move lines for four units')

    @api.model
    def _quality_actual_probe_stock_snapshot(self, scene):
        production = scene['production']
        moves = (production.move_raw_ids | production.move_finished_ids).sorted('id')
        children = self.search([('quality_native_parent_id', '=', production.id)], order='id')
        quants = self.env['stock.quant'].search([
            ('product_id', 'in', (scene['product'] | scene['component']).ids)], order='id')
        return {
            'production': production.read(['state', 'product_qty', 'qty_producing', 'qty_produced']),
            'operations': production.workorder_ids.sorted('id').read(['state', 'qty_produced']),
            'moves': moves.read(['state', 'product_id', 'product_uom_qty', 'quantity']),
            'lines': moves.move_line_ids.sorted('id').read(['move_id', 'quantity', 'consume_line_ids']),
            'valuation': moves.stock_valuation_layer_ids.sorted('id').read(['quantity', 'value', 'stock_move_id']),
            'facts': production.native_stock_fact_ids.sorted('id').read(['unit_id', 'move_line_id']),
            'children': children.read(['state', 'product_qty', 'quality_native_parent_id']),
            'quants': quants.read(['product_id', 'location_id', 'quantity', 'reserved_quantity']),
            'units': scene['units'].sorted('id').read(['name', 'production_id', 'registration_run_id']),
            'admissions': scene['check'].unit_admission_ids.sorted('id').read(['controlled_unit_id', 'state']),
            'executions': production.workorder_ids._quality_native_history().sorted('id').read([
                'controlled_unit_id', 'workorder_id', 'state']),
        }

    @api.model
    def _quality_actual_probe_deny(self, scene, messages):
        before = self._quality_actual_probe_stock_snapshot(scene)
        detail = _expected_denial(scene['production'].action_release_quality_actual_batch, messages)
        _assert(before == self._quality_actual_probe_stock_snapshot(scene),
                'Rejected action changed stock, valuation, operation quantities, unit facts or backorders')
        return dict(detail, stock_unchanged=True, release_action_called=True)

    @api.model
    def _quality_actual_probe_positive(self, scene, progress=None):
        production, check, units = (scene[key] for key in ('production', 'check', 'units'))
        first, last = scene['orders']
        _assert(check.quality_state == 'pass' and len(check.unit_admission_ids.filtered(
            lambda row: row.state == 'confirmed' and not row._get_effectiveness_error())) == 3,
            'Positive fixture lacks three real effective admissions')
        old_samples = check.sample_ids.read(['sample_result', 'controlled_unit_id'])
        old_executions = last._quality_native_history().read(['state', 'controlled_unit_id', 'workorder_id'])
        production.action_release_quality_actual_batch()
        inventory = self._quality_actual_probe_inventory(scene)
        if progress is not None:
            progress.setdefault('inventory', {})['after_release'] = inventory
        children = self.search([('quality_native_parent_id', '=', production.id)])
        raw = production.move_raw_ids.filtered(lambda row: row.state == 'done')
        finished = production.move_finished_ids.filtered(lambda row: row.state == 'done')
        _assert((production.state, production.product_qty, production.qty_produced) == ('done', 3, 3)
                and set(production.workorder_ids.mapped('state')) == {'done'}, 'Native actual batch did not close at three')
        _assert(len(children) == 1 and children.product_qty == 2 and not children.qty_produced
                and children.state not in ('done', 'cancel') and not children._full_release_units()
                and children.procurement_group_id == production.procurement_group_id,
                'Native remainder is not exactly one unmanufactured order for two')
        _assert(len(production.native_stock_fact_ids) == 3
                and set(production.native_stock_fact_ids.unit_id.ids) == set(units.ids)
                and sum(finished.move_line_ids.mapped('quantity')) == 3
                and set(production.native_stock_fact_ids.move_line_id.ids) == set(finished.move_line_ids.ids),
                'Three physical units are not linked to the real finished stock lines')
        _assert(sum(raw.mapped('quantity')) == 6
                and set(finished.move_line_ids.consume_line_ids.ids) == set(raw.move_line_ids.ids)
                and sum(raw.stock_valuation_layer_ids.mapped('quantity')) == -6
                and sum(finished.stock_valuation_layer_ids.mapped('quantity')) == 3,
                'Real component consumption, links or valuation quantities differ from the actual batch')
        self._quality_actual_probe_assert_inventory(inventory)
        _assert(len(self.env['quality.production.unit'].search([('product_id', '=', scene['product'].id)])) == 3
                and not first._quality_native_history()
                and not self.env['quality.check'].search([('workorder_id', '=', first.id)])
                and old_samples == check.sample_ids.read(['sample_result', 'controlled_unit_id'])
                and old_executions == last._quality_native_history().read(['state', 'controlled_unit_id', 'workorder_id']),
                'Native release created fictional units/early checks or rewrote physical evidence')
        return {'finished_quantity': 3, 'component_quantity': 6, 'backorder_quantity': 2,
                'stock_facts': 3, 'release_action_called': True, 'historical_evidence_unchanged': True}

    @api.model
    def _quality_actual_probe_case(self, code, progress):
        _assert(code in SCENARIOS, 'Unknown acceptance scenario: %s' % code)
        if code == 'N6':
            # Public release has no unit argument; legally constructing a foreign
            # membership is itself prohibited by immutable identity constraints.
            # Do not bypass them to pretend the release method was exercised.
            progress['stage'] = 'foreign_identity_safety'
            raise _ProbeSkip('Foreign-unit injection cannot reach the public action without bypassing identity guards; covered by the isolated validator ORM test.')
        progress['stage'] = 'fixture'
        try:
            scene = self._quality_actual_probe_setup({'N1': 'fail', 'N2': 'pending'}.get(code, 'pass'),
                                                   unfinished_first=code == 'N5', progress=progress)
        except AccessError:
            raise
        except UserError as error:
            if code == 'N5' and str(error) in {
                    _(PARTIAL_WO_MESSAGE), _('Не підтверджено виконання попередньої операції для цих виробів.')}:
                raise _ProbeSkip('Native predecessor guard prevents safe N5 fixture preparation; no release tested: %s' % error) from error
            raise
        progress['synthetic_timer_preparation'] = scene['synthetic_timer_preparation']
        progress['shop_floor_timer_start_proven'] = False
        production, check, samples = (scene[key] for key in ('production', 'check', 'samples'))
        progress['stage'] = 'scenario_preparation'
        if code in ('P1', 'N14', 'N7'):
            progress['stage'] = 'native_release_and_stock_assertions'
            progress['release_action_attempted'] = True
            detail = self._quality_actual_probe_positive(scene, progress)
            if code == 'N7':
                progress['stage'] = 'duplicate_release'
                detail.update(self._quality_actual_probe_deny(scene, {
                    _('Це виробниче замовлення вже завершено. Повторний випуск заборонено.')}))
            return dict(detail, status='PASS')
        control_message = lambda sample: _(
            'Перевірка якості %(check)s / %(unit)s: потрібні завершена перевірка з результатом «Придатний», вибрані засоби вимірювальної техніки та відсутність утримань.',
            check=check.display_name, unit=sample.controlled_unit_id.display_name)
        if code == 'N1':
            _assert(check.quality_state == 'fail' and samples[-1:].sample_result == 'fail', 'FAIL fixture is not FAIL')
            messages = {control_message(samples[-1:])}
        elif code == 'N2':
            _assert(check.quality_state == 'none' and samples[-1:].sample_result == 'pending', 'Pending fixture is not pending')
            messages = {control_message(sample) for sample in samples}
        elif code == 'N3':
            admission = check.unit_admission_ids.filtered(lambda row: row.controlled_unit_id == samples[0].controlled_unit_id
                                                          and row.state == 'confirmed')
            _assert(len(admission) == 1, 'Expected a real admission to revoke')
            admission._revoke('Temporary acceptance probe: revoke one admission')
            _assert(admission.state == 'revoked', 'Admission was not revoked')
            messages = {_('Немає чинного допуску інспектора для %(unit)s / точки контролю якості №%(point)s.',
                          unit=admission.controlled_unit_id.display_name, point=scene['point'].id)}
        elif code == 'N4':
            alert = self.env['quality.alert'].create({'name': scene['tag'], 'check_id': check.id,
                                                     'team_id': scene['team'].id})
            alert._bind_matrix_failure_scope(samples[:1], 'Temporary acceptance probe: unresolved hold')
            _assert(alert._matrix_hold_is_open() and check._matrix_alert_hold_error(samples[:1]), 'Alert did not hold the selected sample')
            messages = {control_message(samples[:1])}
        elif code == 'N5':
            unfinished = scene['orders'][0]
            _assert(unfinished.state == 'progress' and unfinished.qty_produced == 3,
                    'First operation must retain its real partial execution and unfinished state')
            messages = {_('Не підтверджено фактичне виконання операції %(operation)s для всієї виготовленої партії.',
                          operation=unfinished.display_name)}
        elif code in ('N8', 'N12'):
            if code == 'N8':
                production.qty_producing = 5
                _assert(len(production._full_release_units()) == 3 and production.qty_producing == 5,
                        'Quantity mismatch was not established')
            else:
                production.action_require_quality_unit_route()
                _assert(production.quality_unit_route_required, 'Required route was not enabled normally')
            messages = {_('Випуск фактичної партії дозволено лише для відкритого замовлення без попереднього випуску, обов’язкового поштучного маршруту, історичних передач та партійного або серійного відстеження. Кількість має точно відповідати виготовленим виробам у штуках.')}
        else:
            raise ValidationError('Unknown acceptance scenario: %s' % code)
        progress['stage'] = 'expected_business_rejection'
        progress['release_action_attempted'] = True
        return dict(self._quality_actual_probe_deny(scene, messages), status='PASS')
