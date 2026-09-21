// Standalone routing regression: node extra-addons/account_vataga/tests/test_invoice_chatter.mjs
// Runs the real invoice/BOM patches in both orders with minimal service doubles.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const invoiceSource = readFileSync(new URL('../static/src/chatter/invoice_chatter.js', import.meta.url), 'utf8');
const bomSource = readFileSync(new URL('../../mrp_vataga/static/src/chatter/bom_chatter.js', import.meta.url), 'utf8');
const stripImports = (source) => source.replace(/^import .*;\r?\n/gm, '');

for (const sources of [[bomSource, invoiceSource], [invoiceSource, bomSource]]) {
    class Chatter {
        setup() { this.state = { thread: {} }; }
        async load(thread, fields) { this.loaded = { thread, fields }; }
    }
    class ThreadService {
        getFetchRoute() { return '/mail/thread/messages'; }
        getFetchParams(thread) { return { thread_model: thread.model, thread_id: thread.id }; }
    }
    const storage = new Map();
    const browser = { localStorage: {
        getItem: (key) => storage.get(key),
        setItem: (key, value) => storage.set(key, value),
    } };
    const patch = (target, extension) => {
        const previous = Object.create(Object.getPrototypeOf(target), Object.getOwnPropertyDescriptors(target));
        Object.setPrototypeOf(extension, previous);
        Object.defineProperties(target, Object.getOwnPropertyDescriptors(extension));
    };
    for (const source of sources) {
        runInNewContext(stripImports(source), { Chatter, ThreadService, browser, patch });
    }
    const service = new ThreadService();
    for (const [model, route, preference] of [
        ['account.move', '/account_vataga/mail/thread/messages', 'hide_invoice_autologs'],
        ['mrp.bom', '/mrp_vataga/mail/thread/messages', 'hide_bom_autologs'],
        ['sale.order', '/mail/thread/messages', null],
    ]) {
        const thread = { type: 'chatter', model, id: 42 };
        assert.equal(service.getFetchRoute(thread), route);
        const params = service.getFetchParams(thread);
        assert.equal(params.thread_model, model);
        if (preference) assert.equal(params[preference], false);
        assert.equal(Object.keys(params).length, preference ? 3 : 2);
    }
    const chatter = new Chatter();
    chatter.props = { threadModel: 'account.move', threadId: 42 };
    chatter.setup();
    assert.equal(chatter.showInvoiceAutologToggle, true);
    assert.equal(chatter.showBomAutologToggle, false);
    await chatter.toggleInvoiceAutologs();
    assert.equal(storage.get('account_vataga.hide_invoice_autologs'), '1');
    assert.equal(storage.has('mrp_vataga.hide_bom_autologs'), false);
    assert.equal(service.getFetchParams({ type: 'chatter', model: 'account.move' }).hide_invoice_autologs, true);
    assert.equal(chatter.loaded.fields[0], 'messages');
    assert.equal(chatter.state.thread.messages.length, 0);
    await chatter.toggleInvoiceAutologs();
    assert.equal(storage.get('account_vataga.hide_invoice_autologs'), '0');
    chatter.props.threadId = false;
    assert.equal(chatter.showInvoiceAutologToggle, false);
    chatter.props = { threadModel: 'mrp.bom', threadId: 42 };
    assert.equal(chatter.showInvoiceAutologToggle, false);
    await chatter.toggleBomAutologs();
    assert.equal(storage.get('mrp_vataga.hide_bom_autologs'), '1');
    assert.equal(storage.get('account_vataga.hide_invoice_autologs'), '0');
}
console.log('PASS: invoice/BOM route chaining, independent preferences and toggles in both load orders');
