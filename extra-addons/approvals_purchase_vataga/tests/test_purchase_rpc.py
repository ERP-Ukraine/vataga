import json

from odoo.tests import HttpCase, tagged

from .common import PurchaseApprovalCase


@tagged('post_install', '-at_install', 'vataga_purchase')
class TestPurchaseRPC(PurchaseApprovalCase, HttpCase):
    def test_rpc_cannot_approve_via_approver_write(self):
        request = self.request()
        request.action_confirm()
        approver = request.approver_ids.filtered(lambda a: a.user_id == self.approver)
        result = self.rpc(self.approver, 'approval.approver', 'write',
                          [[approver.id], {'status': 'approved'}], {'_vataga_workflow': True})
        self.assertIn('error', result)
        request.invalidate_recordset()
        self.assertNotEqual(request.request_status, 'approved')

    def rpc(self, user, model, method, args, context=None):
        self.authenticate(user.login, user.login)
        response = self.url_open('/web/dataset/call_kw', data=json.dumps({
            'jsonrpc': '2.0', 'method': 'call', 'id': 1,
            'params': {'model': model, 'method': method, 'args': args,
                       'kwargs': {'context': context or {}}},
        }), headers={'Content-Type': 'application/json'})
        return response.json()

    def test_rpc_cannot_edit_approved_quantities(self):
        request = self.approve(self.request())
        result = self.rpc(self.buyer, 'approval.product.line', 'write',
                          [request.product_line_ids.ids, {'quantity': 999}])
        self.assertIn('error', result)
        request.product_line_ids.invalidate_recordset()
        self.assertEqual(request.product_line_ids.quantity, 2)

    def test_rpc_context_cannot_forge_history(self):
        request = self.request()
        result = self.rpc(self.owner, 'approval.request', 'write',
                          [[request.id], {'vataga_rfq_created': True}],
                          {'_vataga_internal': True, '_vataga_rfq': True})
        self.assertIn('error', result)
        request.invalidate_recordset()
        self.assertFalse(request.vataga_rfq_created)

    def test_rpc_registry_search_and_read_do_not_leak(self):
        request = self.request([self.line_values(missing=True)])
        result = self.rpc(self.stranger, 'approval.product.line', 'search_read',
                          [[('approval_request_id', '=', request.id)], ['description', 'vataga_product_url']])
        self.assertEqual(result.get('result'), [])
        result = self.rpc(self.stranger, 'approval.product.line', 'read',
                          [request.product_line_ids.ids, ['description']])
        self.assertIn('error', result)
