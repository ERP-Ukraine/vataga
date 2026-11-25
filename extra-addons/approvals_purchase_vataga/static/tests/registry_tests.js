/** @odoo-module **/

import { click, getFixture } from '@web/../tests/helpers/utils';
import { makeView, setupViewRegistries } from '@web/../tests/views/helpers';

QUnit.module('Vataga: compact registry', (hooks) => {
    hooks.beforeEach(() => setupViewRegistries());

    QUnit.test('number delegates to checked server action; URL stays safe and external', async (assert) => {
        let opened = 0;
        const view = await makeView({
            type: 'list', resModel: 'approval.product.line',
            serverData: { models: { 'approval.product.line': {
                fields: { number: { type: 'char' }, url: { type: 'char' } },
                records: [{ id: 1, number: 'APPR00009', url: 'https://example.com/full?item=7' },
                    { id: 2, number: 'VPR/2026/1', url: 'javascript:alert(1)' }],
            } } },
            arch: '<tree create="0" edit="0"><field name="number" widget="vataga_request_link"/><field name="url" widget="vataga_registry_url"/></tree>',
            mockRPC(route, args) {
                if (args.method === 'action_vataga_open_request') {
                    assert.deepEqual(args.args, [[1]]);
                    opened++;
                    return { type: 'ir.actions.act_window', res_model: 'approval.request', res_id: 20 };
                }
            },
        });
        const target = getFixture();
        const link = target.querySelector('a[aria-label="Відкрити URL"]');
        assert.strictEqual(link.getAttribute('href'), 'https://example.com/full?item=7');
        assert.strictEqual(link.getAttribute('title'), 'https://example.com/full?item=7');
        assert.strictEqual(link.target, '_blank');
        assert.ok(link.rel.includes('noopener'));
        assert.containsOnce(target, '[aria-label="Некоректний URL"]');
        assert.containsNone(target, 'a[href^="javascript:"]');
        let returned;
        view.env.services.action.doAction = async (action) => { returned = action; };
        await click(target, '.o_vataga_request_link');
        assert.strictEqual(opened, 1);
        assert.strictEqual(returned.res_id, 20);
    });
});
