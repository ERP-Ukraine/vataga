"""Real Odoo 17 field/API contracts plus isolated policy regression tests.

No database, Enterprise registry or standard approval workflow is simulated.
Set VATAGA_ODOO_SOURCE when the local Community checkout is not on sys.path.
"""
import ast
from collections import defaultdict
from contextlib import nullcontext
from datetime import date, datetime
import importlib
import inspect
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lxml import etree

from support import ROOT, internal

if os.environ.get('VATAGA_ODOO_SOURCE'):
    sys.path.insert(0, os.environ['VATAGA_ODOO_SOURCE'])
try:
    from odoo import api, fields, models
    from odoo.exceptions import AccessError, UserError
    from odoo.modules.module import initialize_sys_path
    from odoo.tools import config
    from odoo.tools.safe_eval import safe_eval
except ImportError as error:
    raise unittest.SkipTest('Odoo 17 Python runtime required for these contract tests') from error

config['addons_path'] = str(ROOT.parent)
initialize_sys_path()
line_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.approval_product_line')
approver_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.approval_approver')
request_module = importlib.import_module('odoo.addons.approvals_purchase_vataga.models.approval_request')
workflow_internal = importlib.import_module('odoo.addons.approvals_purchase_vataga.internal')


class RequestSet(list):
    def filtered(self, predicate):
        return RequestSet(item for item in self if predicate(item))

    def _vataga_check_access(self, mode='read'):
        for item in self:
            if mode == 'write' and not item.write_allowed:
                raise AccessError('No request edit permission')

    def _vataga_lock(self):
        self._vataga_check_access('read')


def request(purchase=True, locked=False, write_allowed=True):
    return SimpleNamespace(_vataga_is_purchase=lambda: purchase,
                           _vataga_is_locked=lambda: locked, write_allowed=write_allowed)


class TestOdooContracts(unittest.TestCase):
    def test_existing_purchase_lock_and_rfq_links_do_not_need_new_install_flags(self):
        # Isolated method contract, not a first-install database simulation.
        # All new flags are False, as for records predating the addon.
        class Legacy:
            approval_type = 'purchase'
            vataga_purchase_managed = False
            vataga_approved_once = False
            vataga_legacy_locked = False
            vataga_rfq_created = False
            request_status = 'new'
            product_line_ids = SimpleNamespace(purchase_order_line_id=False)
            ensure_one = lambda self: None
            _vataga_is_purchase = request_module.ApprovalRequest._vataga_is_purchase
            _vataga_is_processed = request_module.ApprovalRequest._vataga_is_processed
            _vataga_is_locked = request_module.ApprovalRequest._vataga_is_locked
        old = Legacy()
        self.assertFalse(old._vataga_is_locked())
        for status in ('pending', 'approved', 'refused', 'cancel'):
            old.request_status = status
            self.assertTrue(old._vataga_is_locked())
        old.request_status = 'new'
        old.product_line_ids = SimpleNamespace(purchase_order_line_id=object())
        self.assertTrue(old._vataga_is_locked())
        old.request_status = 'approved'
        with patch.object(request_module, '_', lambda value: value):
            self.assertEqual(request_module.ApprovalRequest._vataga_rfq_reason(old), 'ЗНКП вже сформовано.')

    def test_first_projection_of_existing_identity_never_assigns_new_sequence(self):
        for automatic, name, expected in ((True, 'APPR-EXISTING', 'APPR-EXISTING'),
                                           (False, 'Existing manual subject', False)):
            old = SimpleNamespace(automated_sequence=automatic, name=name,
                                  vataga_number=False, vataga_purchase_subject=False)
            request_module.ApprovalRequest._compute_vataga_identity([old])
            self.assertEqual(old.vataga_display_number, expected)
            self.assertEqual(old.name, name)
            self.assertFalse(old.vataga_number)

    def test_native_related_setup_uses_manual_employee_comodel_without_replacing_source(self):
        from copy import copy
        link = fields.Many2one('approval.request')
        link._setup_attrs(SimpleNamespace(_name='approval.product.line', _inherits={}), 'approval_request_id')
        link._setup_done = True
        for projection, source_name in [('vataga_employee_initiator_id', 'x_studio_iniciation'),
                                        ('vataga_employee_recipient_id', 'x_studio_who_recived')]:
            source = fields.Many2one('hr.employee')
            source._setup_attrs(SimpleNamespace(_name='approval.request', _inherits={}), source_name)
            source.manual, source._setup_done = True, True
            related = copy(getattr(line_module.ApprovalProductLine, projection))
            related._setup_attrs(SimpleNamespace(_name='approval.product.line', _inherits={}), projection)
            model = SimpleNamespace(pool={
                'approval.product.line': SimpleNamespace(_fields={'approval_request_id': link}),
                'approval.request': SimpleNamespace(_fields={source_name: source}),
            })
            related.setup_related(model)
            self.assertEqual(related.type, 'many2one')
            self.assertEqual(related.comodel_name, 'hr.employee')
            self.assertTrue(related.store)
            self.assertFalse(related.compute_sudo)
            self.assertIs(related.related_field, source)
            self.assertTrue(source.manual)
            self.assertEqual(source.comodel_name, 'hr.employee')

    def test_late_erp_period_pair_is_localized_and_locked_with_native_inheritance(self):
        from odoo.tools.template_inheritance import apply_inheritance_specs
        source = etree.fromstring(b'<form><group><field name="date_start" readonly="True"/><field name="date_end" readonly="True"/></group><group><field name="date_start"/><field name="date_end"/></group></form>')
        document = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        record = document.xpath("//record[@id='view_approval_request_vataga_late_period_ui']")[0]
        self.assertEqual(record.xpath("field[@name='inherit_id']/@ref"), ['approvals.approval_request_view_form'])
        specs = etree.Element('data')
        from copy import deepcopy
        for spec in record.xpath("field[@name='arch']/xpath"):
            specs.append(deepcopy(spec))
        result = apply_inheritance_specs(source, specs)
        for name, title in [('date_start', 'Дата початку'), ('date_end', 'Дата закінчення')]:
            original, late = result.xpath(".//field[@name='%s']" % name)
            self.assertEqual(original.get('readonly'), 'True')
            self.assertEqual(late.get('string'), title)
            self.assertTrue(safe_eval(late.get('readonly'), {'vataga_lines_locked': True}))
            self.assertFalse(safe_eval(late.get('readonly'), {'vataga_lines_locked': False}))

    def test_purchase_products_page_remains_visible_for_native_no_product(self):
        tree = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        expr = tree.xpath("//xpath[@expr=\"//page[@name='products']\"]/attribute[@name='invisible']/text()")[0]
        for approval_type, has_product, hidden in (
            ('purchase', 'no', False), ('purchase', 'required', False),
            (False, 'no', True), (False, 'optional', False),
        ):
            self.assertEqual(safe_eval(expr, {'approval_type': approval_type, 'has_product': has_product}), hidden)

    def test_registry_date_widget_uses_each_real_source_type(self):
        arch = etree.fromstring(b'<tree><field name="vataga_date_start" optional="hide"/><field name="vataga_date_end"/><field name="vataga_request_date"/></tree>')
        for start_type, end_type in [('date', 'date'), ('datetime', 'datetime'), ('date', 'datetime')]:
            result = internal.registry_period_arch(arch, {'date_start': start_type, 'date_end': end_type})
            for name, kind in [('date_start', start_type), ('date_end', end_type)]:
                self.assertEqual(result.xpath(".//field[@name='vataga_%s']" % name)[0].get('widget'),
                                 'vataga_calendar_date' if kind == 'date' else None)
            self.assertIsNone(result.xpath(".//field[@name='vataga_request_date']")[0].get('widget'))
            self.assertEqual(result[0].get('optional'), 'hide')
        self.assertFalse(arch.xpath('.//*[@widget]'), 'Never mutate a shared combined architecture')

    def test_purchase_quantity_remains_visible_without_native_header_quantity(self):
        tree = etree.parse(str(ROOT / 'views/approval_request_views.xml'))
        expression = tree.xpath("//record[@id='view_approval_product_line_vataga_purchase']//xpath[@expr=\"//field[@name='quantity']\"]/attribute[@name='column_invisible']/text()")
        self.assertEqual(len(expression), 1)
        for approval_type, has_quantity, hidden in (
            ('purchase', 'no', False), ('purchase', 'required', False),
            (False, 'no', True), (False, 'optional', False),
        ):
            with self.subTest(approval_type=approval_type, has_quantity=has_quantity):
                parent = SimpleNamespace(approval_type=approval_type, has_quantity=has_quantity)
                self.assertEqual(safe_eval(expression[0], {'parent': parent}), hidden)

    def test_old_date_related_fails_against_datetime_source(self):
        link = fields.Many2one()
        link.comodel_name = 'approval.request'
        link._setup_done = True
        source = fields.Datetime()
        source._setup_done = True
        old = fields.Date()
        old.model_name = 'approval.product.line'
        old.name = 'vataga_date_start'
        old.related = 'approval_request_id.date_start'
        model = SimpleNamespace(pool={
            'approval.product.line': SimpleNamespace(_fields={'approval_request_id': link}),
            'approval.request': SimpleNamespace(_fields={'date_start': source}),
        })
        with self.assertRaisesRegex(TypeError, 'inconsistent'):
            old.setup_related(model)

    def test_datetime_projection_preserves_time(self):
        start, end = datetime(2026, 11, 24, 13, 17, 39), datetime(2026, 11, 25, 22, 18, 41)
        line = SimpleNamespace(approval_request_id=SimpleNamespace(date_start=start, date_end=end))
        line_module.ApprovalProductLine._compute_vataga_date_start([line])
        line_module.ApprovalProductLine._compute_vataga_date_end([line])
        self.assertEqual(line.vataga_date_start, start)
        self.assertEqual(line.vataga_date_end, end)
        self.assertEqual(line_module.ApprovalProductLine.vataga_date_start.type, 'datetime')

    def test_erp_date_projection_and_empty_values(self):
        line = SimpleNamespace(approval_request_id=SimpleNamespace(date_start=date(2026, 11, 24), date_end=False))
        line_module.ApprovalProductLine._compute_vataga_date_start([line])
        line_module.ApprovalProductLine._compute_vataga_date_end([line])
        self.assertEqual(line.vataga_date_start, datetime(2026, 11, 24))
        self.assertFalse(line.vataga_date_end)

    def test_search_and_compute_hook_signatures_match_real_orm(self):
        actual = inspect.signature(models.BaseModel._search)
        override = inspect.signature(line_module.ApprovalProductLine._search)
        self.assertEqual(list(actual.parameters), list(override.parameters))
        self.assertEqual(list(inspect.signature(models.BaseModel._compute_field_value).parameters),
                         list(inspect.signature(line_module.ApprovalProductLine._compute_field_value).parameters))
        from odoo.addons.base.models.ir_ui_view import Model as ViewProvider
        self.assertEqual(list(inspect.signature(ViewProvider._get_view).parameters),
                         list(inspect.signature(line_module.ApprovalProductLine._get_view).parameters))

    def test_company_list_context_is_supported_by_odoo_cache(self):
        field = object()
        env = SimpleNamespace(_cache_key={}, uid=7, su=False, company=SimpleNamespace(id=1),
                              context={'allowed_company_ids': [1, 2]},
                              registry=SimpleNamespace(field_depends_context={field: ('uid', 'allowed_company_ids')}))
        self.assertEqual(api.Environment.cache_key(env, field), ((7, False), (1, 2)))

    def test_readonly_derived_input_is_rejected_but_editable_fields_remain_allowed(self):
        actor = SimpleNamespace(_fields={
            'approval_type': SimpleNamespace(readonly=True, related='category_id.approval_type', compute=None),
            'approval_minimum': SimpleNamespace(readonly=True, related=None, compute='_compute_minimum'),
            'name': SimpleNamespace(readonly=False, related=None, compute=None),
            'request_owner_id': SimpleNamespace(readonly=False, related=None, compute=None),
        })
        with patch.object(request_module, '_', lambda message, *args, **kwargs: message):
            for values in ({'approval_type': False}, {'approval_minimum': 0}):
                with self.subTest(values=values), self.assertRaises(AccessError):
                    request_module.ApprovalRequest._vataga_check_derived_input(actor, values)
            request_module.ApprovalRequest._vataga_check_derived_input(actor, {'name': 'Draft'})

    def test_nonstored_quantity_compute_delegates_without_sql(self):
        tree = ast.parse((ROOT / 'models/approval_product_line.py').read_text(encoding='utf-8'))
        cls = next(item for item in tree.body if isinstance(item, ast.ClassDef))
        method = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == '_compute_field_value')
        body = ast.ClassDef(name=cls.name, bases=[ast.Name(id='Base', ctx=ast.Load())], keywords=[],
                            body=[method], decorator_list=[])
        module = ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[]))
        class Base:
            def _compute_field_value(self, field):
                return 'native-compute'
        namespace = {'Base': Base}
        exec(compile(module, 'actual-compute-hook', 'exec'), namespace)
        record = namespace[cls.name]()
        field = fields.Float()
        field.name, field.store = 'quantity', False
        record.pool = SimpleNamespace(field_computed={field: [field]})
        self.assertEqual(record._compute_field_value(field), 'native-compute')

    def test_lock_refresh_preserves_protected_fields_and_inverses(self):
        direct, relation, inverse, ordinary = [fields.Char() for _ in range(4)]
        for field, name in zip((direct, relation, inverse, ordinary), ('computing', 'relation', 'inverse', 'ordinary')):
            field.name = name
        env = SimpleNamespace(is_protected=lambda field, record: field is direct,
                              protected=lambda field: [1] if field is inverse else [])
        class Records(list):
            pass
        records = Records([object()])
        records.env = env
        records._fields = {field.name: field for field in (direct, relation, ordinary)}
        records.pool = SimpleNamespace(field_inverses=defaultdict(tuple, {relation: (inverse,)}))
        self.assertEqual(workflow_internal.refreshable_fields(records), ['ordinary'])

    def test_repeated_confirm_preserves_legacy_approval_without_inventing_snapshot(self):
        # Exercise the actual adapter around a successful native no-op. This
        # tests the legacy boundary, not the unavailable Enterprise workflow.
        tree = ast.parse((ROOT / 'models/approval_request.py').read_text(encoding='utf-8'))
        source_class = next(item for item in tree.body if isinstance(item, ast.ClassDef))
        methods = [item for item in source_class.body if isinstance(item, ast.FunctionDef)
                   and item.name in {'action_confirm', '_vataga_preserve_approval_lock', '_vataga_capture_approval'}]
        cls = ast.ClassDef(name='ApprovalRequest', bases=[ast.Name(id='Base', ctx=ast.Load())],
                           keywords=[], body=methods, decorator_list=[])
        class Base:
            def __iter__(self):
                return iter([self])
            def filtered(self, predicate):
                return self if predicate(self) else []
            def sudo(self):
                return self
            def with_context(self, **context):
                return self
            def write(self, values):
                self.__dict__.update(values)
            def _vataga_is_purchase(self):
                return True
            def _vataga_lock(self):
                pass
            def _vataga_validate_lines(self):
                pass
            def action_confirm(self):
                return 'native-success'
        namespace = {'Base': Base, 'INTERNAL': workflow_internal.INTERNAL, 'WORKFLOW': workflow_internal.WORKFLOW,
                     'public_result': workflow_internal.public_result}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                     'actual-confirm-adapter', 'exec'), namespace)
        record = namespace['ApprovalRequest']()
        record.approval_type, record.request_status = 'purchase', 'approved'
        record.vataga_approved_once, record.vataga_legacy_locked = False, False
        recorded = []
        record.product_line_ids = [SimpleNamespace(product_id=True, product_uom_id=True,
                                                   _vataga_record_approval=lambda: recorded.append(True))]
        record.env = SimpleNamespace(cr=SimpleNamespace(savepoint=nullcontext))
        self.assertEqual(record.action_confirm(), 'native-success')
        self.assertTrue(record.vataga_approved_once)
        self.assertFalse(recorded)


class TestApproverGuards(unittest.TestCase):
    def guard(self, records, values, workflow=None, creating=False):
        actor = SimpleNamespace(env=SimpleNamespace(context={'_vataga_workflow': workflow}))
        with patch.object(approver_module, '_', lambda message, *args, **kwargs: message):
            approver_module.ApprovalApprover._vataga_guard_mutation(actor, RequestSet(records), values, creating)

    def test_direct_status_forgery_is_rejected_in_draft(self):
        for status in ('approved', 'pending', 'refused', 'cancel'):
            with self.subTest(status=status), self.assertRaises(AccessError):
                self.guard([request()], {'status': status})

    def test_json_context_cannot_unlock_submitted_approvers(self):
        for token in (True, 'WORKFLOW', 1, None):
            with self.subTest(token=token), self.assertRaises(UserError):
                self.guard([request(locked=True)], {'status': 'approved'}, token)

    def test_pending_unlink_or_clear_is_rejected(self):
        with self.assertRaises(UserError):
            self.guard([request(locked=True)], {})

    def test_native_workflow_does_not_require_request_edit_permission(self):
        self.guard([request(locked=True, write_allowed=False)], {'status': 'approved'}, workflow_internal.WORKFLOW)

    def test_draft_configuration_requires_request_edit_permission(self):
        with self.assertRaises(AccessError):
            self.guard([request(write_allowed=False)], {'required': False})

    def test_other_approval_types_are_unchanged(self):
        self.guard([request(purchase=False, locked=True, write_allowed=False)], {'status': 'approved'})

    def test_initial_new_status_remains_allowed(self):
        self.guard([request()], {'status': 'new'}, creating=True)


if __name__ == '__main__':
    unittest.main()
