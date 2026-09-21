/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";

const preferenceKey = "account_vataga.hide_invoice_autologs";
const hidden = () => browser.localStorage.getItem(preferenceKey) === "1";
const isInvoiceThread = (thread) => thread?.type === "chatter" && thread.model === "account.move";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.hideInvoiceAutologs = hidden();
    },
    get showInvoiceAutologToggle() {
        return this.props.threadModel === "account.move" && Boolean(this.props.threadId);
    },
    async toggleInvoiceAutologs() {
        if (!this.showInvoiceAutologToggle) {
            return;
        }
        this.state.hideInvoiceAutologs = !this.state.hideInvoiceAutologs;
        browser.localStorage.setItem(preferenceKey, this.state.hideInvoiceAutologs ? "1" : "0");
        const thread = this.state.thread;
        Object.assign(thread, {
            messages: [], pendingNewMessages: [], isLoaded: false,
            loadOlder: false, loadNewer: false, scrollTop: "bottom",
        });
        await this.load(thread, ["messages"]);
    },
});

// Delegate all other models through super, regardless of the BOM patch load order.
patch(ThreadService.prototype, {
    getFetchRoute(thread) {
        return isInvoiceThread(thread)
            ? "/account_vataga/mail/thread/messages"
            : super.getFetchRoute(...arguments);
    },
    getFetchParams(thread) {
        const params = super.getFetchParams(...arguments);
        if (isInvoiceThread(thread)) {
            params.hide_invoice_autologs = hidden();
        }
        return params;
    },
});
