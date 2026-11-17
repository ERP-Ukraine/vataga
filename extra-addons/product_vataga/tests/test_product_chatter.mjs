import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const readPatch = (path) => readFileSync(new URL(path, import.meta.url), "utf8")
    .replace(/^import .*;\r?$/gm, "");
const product = readPatch("../static/src/chatter/product_chatter.js");
const invoice = readPatch("../../account_vataga/static/src/chatter/invoice_chatter.js");
const bom = readPatch("../../mrp_vataga/static/src/chatter/bom_chatter.js");

for (const order of [
    [product, invoice, bom], [product, bom, invoice],
    [invoice, product, bom], [invoice, bom, product],
    [bom, product, invoice], [bom, invoice, product],
]) {
    class Chatter {
        setup() {
            this.state = { thread: { messages: [42] } };
        }
    }
    class ThreadService {
        getFetchRoute() {
            return "/mail/thread/messages";
        }
        getFetchParams(thread) {
            return { thread_model: thread.model };
        }
        async fetchNewMessages(thread) {
            assert.equal(thread.messages.length, 0);
            this.refreshed = thread;
        }
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
    for (const source of order) {
        runInNewContext(source, {
            Chatter, ThreadService, browser, patch, session: { db: "test", uid: 11 },
        });
    }
    const service = new ThreadService();
    for (const [model, route, preference] of [
        ["product.template", "product_vataga", "hide_product_autologs"],
        ["product.product", "product_vataga", "hide_product_autologs"],
        ["account.move", "account_vataga", "hide_invoice_autologs"],
        ["mrp.bom", "mrp_vataga", "hide_bom_autologs"],
        ["sale.order", "", null],
    ]) {
        const thread = { type: "chatter", model };
        assert.equal(service.getFetchRoute(thread), `${route ? "/" + route : ""}/mail/thread/messages`);
        const params = service.getFetchParams(thread);
        assert.equal(params.thread_model, model);
        assert.equal(Object.keys(params).length, preference ? 2 : 1);
        if (preference) {
            assert.equal(params[preference], false);
        }
    }
    const chatter = new Chatter();
    chatter.props = { threadModel: "product.template", threadId: 11 };
    chatter.threadService = service;
    chatter.setup();
    await chatter.toggleProductAutologs();
    assert.equal(storage.get("product_vataga.hide_autologs.test.11"), "1");
    assert.equal(storage.size, 1);
    assert.equal(service.refreshed, chatter.state.thread);
    assert.equal(service.getFetchParams({ type: "chatter", model: "product.product" }).hide_product_autologs, true);
    await chatter.toggleProductAutologs();
    assert.equal(storage.get("product_vataga.hide_autologs.test.11"), "0");
    chatter.props.threadId = false;
    assert.equal(chatter.showProductAutologToggle, false);
}

console.log("PASS: product, invoice and BOM chatter patches in all six load orders");
