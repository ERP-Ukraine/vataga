/** @odoo-module **/

import { click, getFixture } from '@web/../tests/helpers/utils';
import { makeView, setupViewRegistries } from '@web/../tests/views/helpers';

QUnit.module('Vataga: compact registry', (hooks) => {
    hooks.beforeEach(() => setupViewRegistries());

    QUnit.test('cancel caption is local to registry badge', async (assert) => {
        await makeView({
            type: 'list', resModel: 'vataga.status.test',
            serverData: { models: { 'vataga.status.test': {
                fields: { status: { type: 'selection', selection: [['cancel', 'Скасувати']] },
                          native: { type: 'selection', selection: [['cancel', 'Скасувати']] } },
                records: [{ id: 1, status: 'cancel', native: 'cancel' }],
            } } },
            arch: '<tree><field name="status" widget="vataga_registry_status" decoration-muted="status == \'cancel\'"/><field name="native" widget="badge"/></tree>',
        });
        const target = getFixture();
        assert.strictEqual(target.querySelector('[name="status"]').textContent.trim(), 'Скасовано');
        assert.strictEqual(target.querySelector('[name="native"]').textContent.trim(), 'Скасувати');
        assert.containsOnce(target, '[name="status"] .text-bg-muted');
    });

    QUnit.test('fresh list hides optional data and formats datetime without changing it', async (assert) => {
        const view = await makeView({
            type: 'list', resModel: 'vataga.registry.display.test',
            serverData: { models: { 'vataga.registry.display.test': {
                fields: { created: { type: 'datetime' }, quantity: { type: 'float' }, extra: { type: 'char' } },
                records: [{ id: 1, created: '2026-11-25 14:35:47', quantity: 1.25, extra: 'Hidden by default' }],
            } } },
            arch: `<tree create="0" edit="0"><field name="created" widget="datetime" options="{'show_time': False}"/>
                <field name="quantity"/><field name="extra" optional="hide"/></tree>`,
        });
        const target = getFixture();
        assert.containsNone(target, 'td[name="extra"]');
        assert.containsNone(target, '.o_data_cell input');
        assert.notOk(target.querySelector('td[name="created"]').textContent.includes(':'));
        assert.strictEqual(view.model.root.records[0].data.created.second, 47);
        assert.strictEqual(view.model.root.records[0].data.quantity, 1.25);
    });

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
                    return { type: 'ir.actions.act_window', res_model: 'approval.request', res_id: 20,
                        views: [[false, 'form']] };
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
        await click(target, '.o_vataga_request_link[title="APPR00009"]');
        assert.strictEqual(opened, 1);
        assert.strictEqual(returned.res_id, 20);
        assert.deepEqual(returned.views, [[false, 'form']]);
    });
});
