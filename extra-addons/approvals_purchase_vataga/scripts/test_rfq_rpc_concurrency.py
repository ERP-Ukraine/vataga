"""Two REAL RFQ RPC calls. Run ONLY on a disposable local Enterprise DB.

Requires a fresh approved, ready purchase request owned by/assigned to the
test buyer. Creates RFQs in that test DB. No production/staging URLs allowed.
Credentials are read from environment and never printed.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Barrier
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


class RPCFailure(Exception):
    pass


def main():
    url = os.environ['VATAGA_TEST_URL'].rstrip('/')
    parsed = urlsplit(url)
    assert parsed.hostname in ('localhost', '127.0.0.1'), 'Disposable LOCAL Enterprise DB only'
    database = os.environ['VATAGA_TEST_DB']
    login = os.environ['VATAGA_TEST_LOGIN']
    password = os.environ['VATAGA_TEST_PASSWORD']
    request_id = int(os.environ['VATAGA_TEST_REQUEST_ID'])
    def transport(service, method, arguments):
        payload = json.dumps({'jsonrpc': '2.0', 'method': 'call', 'id': 1,
                              'params': {'service': service, 'method': method, 'args': arguments}}).encode()
        response = json.load(urlopen(Request(url + '/jsonrpc', data=payload,
                                            headers={'Content-Type': 'application/json'}), timeout=45))
        if 'error' in response:
            raise RPCFailure(response['error'].get('data', {}).get('message', 'RPC operation failed'))
        return response['result']

    uid = transport('common', 'authenticate', [database, login, password, {}])
    assert uid, 'Test buyer authentication failed'

    def call(model, method, args, kwargs=None):
        return transport('object', 'execute_kw', [database, uid, password, model, method, args, kwargs or {}])

    request = call('approval.request', 'read', [[request_id]], {
        'fields': ['request_status', 'vataga_can_create_rfq', 'vataga_rfq_created', 'product_line_ids']})[0]
    assert request['request_status'] == 'approved' and request['vataga_can_create_rfq']
    assert not request['vataga_rfq_created'], 'Use a FRESH disposable test request'
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=15)
        try:
            call('approval.request', 'action_create_purchase_orders', [[request_id]])
            return 'created'
        except RPCFailure as error:
            if 'ЗНКП вже сформовано' not in str(error):
                raise
            return 'blocked'

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        outcomes = [future.result(timeout=90) for future in futures]
    assert sorted(outcomes) == ['blocked', 'created'], outcomes
    after = call('approval.request', 'read', [[request_id]], {'fields': ['vataga_rfq_created']})[0]
    assert after['vataga_rfq_created']
    lines = call('approval.product.line', 'read', [request['product_line_ids']], {'fields': ['purchase_order_line_id']})
    assert all(line['purchase_order_line_id'] for line in lines)
    print('PASS: two native RFQ RPC calls:', outcomes)


if __name__ == '__main__':
    main()
