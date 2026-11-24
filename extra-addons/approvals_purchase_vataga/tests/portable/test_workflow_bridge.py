from pathlib import Path
import tempfile
import types
import unittest
import importlib.util
import sys

from support import bridge


GUARD = '''
def action_approve(self, approver=None):
    if self.approval_type == 'purchase':
        if any(not line.product_id for line in self.product_line_ids):
            raise UserError(_('Choose a product.'))
    return super().action_approve(approver)
'''


class TestSourceProof(unittest.TestCase):
    def test_validation_only_layer(self):
        self.assertEqual(bridge.validation_only_source(GUARD), (True, True))

    def test_rejects_decorators_that_may_check_permissions(self):
        self.assertFalse(bridge.validation_only_source('@security_decorator\n' + GUARD.strip())[0])

    def test_recordset_loop_and_forwarding(self):
        source = '''
def action_approve(self, approver=None):
    for request in self:
        if request.approval_type == 'purchase' and any(not l.product_id for l in request.product_line_ids):
            raise UserError('Choose a product.')
    return super().action_approve(approver)
'''
        self.assertTrue(bridge.validation_only_source(source)[0])

    def test_not_all_and_ensure_one(self):
        source = GUARD.replace("    if self.approval_type", "    self.ensure_one()\n    if self.approval_type")
        source = source.replace('any(not line.product_id for', 'not all(line.product_id for')
        self.assertEqual(bridge.validation_only_source(source), (True, True))

    def test_rejects_additional_security_checks_and_side_effects(self):
        extras = [
            '    self.check_access_rights("write")\n',
            '    self.message_post(body="Approved")\n',
            '    if not self.env.user.has_group("approvals.group_approval_user"):\n        raise UserError("Forbidden")\n',
            '    self.request_status = "approved"\n',
            '    self._update_activities()\n',
        ]
        for extra in extras:
            with self.subTest(extra=extra):
                source = GUARD.replace('    return super()', extra + '    return super()')
                self.assertFalse(bridge.validation_only_source(source)[0])

    def test_rejects_altered_forwarding_or_nonpurchase_checks(self):
        for source in (
            GUARD.replace('action_approve(approver)', 'action_approve(None)'),
            GUARD.replace("== 'purchase'", "!= 'purchase'"),
            GUARD.replace("== 'purchase'", "== 'sale'"),
            GUARD.replace('product_id', 'required'),
            GUARD.replace('UserError(_(\'Choose a product.\'))', 'self._security_error()'),
            GUARD.replace('if any(', 'if self._can_bypass() and any('),
            GUARD.replace('return super().action_approve(approver)', 'super().action_approve(approver)\n    return True'),
        ):
            with self.subTest(source=source):
                self.assertFalse(bridge.validation_only_source(source)[0])


class TestCooperativeMRO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Synthetic inheritance UNIT fixture, not an Enterprise ORM substitute.
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / 'purchase_layer.py'
        source = '''
class UserError(Exception): pass
def _(text): return text
class Workflow:
    def action_approve(self, approver=None):
        self.events.append(('workflow', approver))
        if not self.allowed:
            raise UserError('Not an approver')
        return 'native-result'
class PurchaseLayer(Workflow):
'''
        source += '\n'.join('    ' + line for line in GUARD.strip().splitlines()) + '\n'
        source += '''
class OtherInstalledModule(PurchaseLayer):
    def action_approve(self, approver=None):
        self.events.append(('other-module', approver))
        return super().action_approve(approver)
'''
        path.write_text(source, encoding='utf-8')
        name = 'odoo.addons.approvals_purchase._vataga_unit_fixture'
        spec = importlib.util.spec_from_file_location(name, path)
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[name] = cls.module
        spec.loader.exec_module(cls.module)
        cls.module.OtherInstalledModule.__module__ = 'odoo.addons.other_installed_module.models.approval_request'
        bridge.install_bridge(cls.module.OtherInstalledModule)
        assert getattr(cls.module.PurchaseLayer.action_approve, '_vataga_validation_bridge', False)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop(cls.module.__name__, None)
        cls.tmp.cleanup()

    def record(self, purchase=True, allowed=True, missing=True):
        record = self.module.OtherInstalledModule()
        record.env = types.SimpleNamespace(cr=object(), uid=7)
        record.ids = [17]
        record._fields = {'vataga_approved_once': True}
        record.approval_type = 'purchase' if purchase else 'other'
        record.product_line_ids = [types.SimpleNamespace(product_id=not missing)]
        record.events = []
        record.allowed = allowed
        def ensure_one():
            if len(record.ids) != 1:
                raise ValueError('Expected singleton')
            return record
        record.ensure_one = ensure_one
        return record

    def test_preserves_modules_above_and_workflow_below_purchase(self):
        record = self.record()
        with bridge.approval_scope(record):
            self.assertEqual(record.action_approve(41), 'native-result')
        self.assertEqual(record.events, [('other-module', 41), ('workflow', 41)])

    def test_preserves_workflow_authorization_failure(self):
        record = self.record(allowed=False)
        with bridge.approval_scope(record), self.assertRaisesRegex(self.module.UserError, 'Not an approver'):
            record.action_approve()

    def test_does_not_skip_original_guard_outside_private_scope(self):
        record = self.record()
        with self.assertRaisesRegex(self.module.UserError, 'Choose a product'):
            record.action_approve()

    def test_scope_is_bound_to_cursor_user_and_records(self):
        authorized = self.record()
        for field, value in (('uid', 8), ('cr', object())):
            record = self.record()
            record.env = types.SimpleNamespace(cr=authorized.env.cr, uid=authorized.env.uid)
            setattr(record.env, field, value)
            with bridge.approval_scope(authorized), self.assertRaises(self.module.UserError):
                record.action_approve()
        other = self.record()
        other.env = authorized.env
        other.ids = [99]
        with bridge.approval_scope(authorized), self.assertRaises(self.module.UserError):
            other.action_approve()

    def test_other_approval_types_keep_original_behavior(self):
        record = self.record(purchase=False)
        self.assertEqual(record.action_approve(9), 'native-result')
        self.assertEqual(record.events, [('other-module', 9), ('workflow', 9)])

    def test_scope_restored_after_error(self):
        record = self.record(allowed=False)
        with self.assertRaises(self.module.UserError):
            with bridge.approval_scope(record):
                record.action_approve()
        self.assertIsNone(bridge.APPROVAL_SCOPE.get())

    def test_multiple_registries_do_not_stack_wrappers(self):
        original = self.module.PurchaseLayer.action_approve
        bridge.install_bridge(self.module.OtherInstalledModule)
        self.assertIs(original, self.module.PurchaseLayer.action_approve)

    def test_implicit_singleton_check_is_preserved(self):
        record = self.record()
        record.ids = [17, 18]
        with bridge.approval_scope(record), self.assertRaisesRegex(ValueError, 'Expected singleton'):
            record.action_approve()
