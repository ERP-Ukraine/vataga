/** @odoo-module **/

import { click, getFixture } from '@web/../tests/helpers/utils';
import { makeView, setupViewRegistries } from '@web/../tests/views/helpers';
import { formatDate } from '@web/core/l10n/dates';
import { calendarDateText } from '@approvals_purchase_vataga/calendar_date_field';

QUnit.module('Vataga: calendar source dates', () => {
    QUnit.test('calendar date survives negative and positive zones and DST', (assert) => {
        for (const iso of ['2026-11-24T00:00:00Z', '2026-03-29T00:00:00Z', '2026-10-25T00:00:00Z']) {
            const utc = luxon.DateTime.fromISO(iso, { zone: 'UTC' });
            for (const zone of ['Pacific/Honolulu', 'Europe/Kyiv', 'Pacific/Kiritimati']) {
                assert.strictEqual(calendarDateText(utc.setZone(zone)), formatDate(utc));
            }
        }
        assert.strictEqual(calendarDateText(false), '');
    });
});

QUnit.module('Vataga: resolve from readonly purchase lines', (hooks) => {
    hooks.beforeEach(() => setupViewRegistries());

    QUnit.test('object button remains active while inline edits stay disabled', async (assert) => {
        let calls = 0;
        await makeView({
            type: 'form',
            resModel: 'approval.request',
            resId: 1,
            serverData: {
                models: {
                    'approval.request': {
                        fields: { product_line_ids: { string: 'Products', type: 'one2many', relation: 'approval.product.line' } },
                        records: [{ id: 1, product_line_ids: [1, 2] }],
                    },
                    'approval.product.line': {
                        fields: {
                            quantity: { type: 'float', string: 'Quantity' },
                            vataga_can_resolve: { type: 'boolean', string: 'Can resolve' },
                        },
                        records: [{ id: 1, quantity: 2, vataga_can_resolve: true },
                            { id: 2, quantity: 1, vataga_can_resolve: false }],
                    },
                },
            },
            arch: `<form><field name="product_line_ids" readonly="1"><tree editable="bottom">
                <field name="quantity"/>
                <field name="vataga_can_resolve" column_invisible="True"/>
                <button name="action_vataga_resolve" type="object" string="Створити товар"
                    invisible="not vataga_can_resolve"/>
            </tree></field></form>`,
            mockRPC(route, args) {
                if (route === '/web/dataset/call_button') {
                    assert.strictEqual(args.model, 'approval.product.line');
                    assert.strictEqual(args.method, 'action_vataga_resolve');
                    assert.deepEqual(args.args, [[1]]);
                    calls++;
                    return false;
                }
            },
        });
        const target = getFixture();
        assert.containsOnce(target, 'button[name="action_vataga_resolve"]');
        assert.containsNone(target, '.o_field_widget[name="quantity"] input');
        assert.notOk(target.querySelector('button[name="action_vataga_resolve"]').disabled);
        await click(target, 'button[name="action_vataga_resolve"]');
        assert.strictEqual(calls, 1);
    });
});
