/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

const productModels = new Set(["product.template", "product.product"]);
const preferenceKey = `product_vataga.hide_autologs.${session.db}.${session.uid}`;
const hidden = () => browser.localStorage.getItem(preferenceKey) === "1";
const isProductThread = (thread) => thread?.type === "chatter" && productModels.has(thread.model);

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.hideProductAutologs = hidden();
        this.state.reloadingProductAutologs = false;
    },
    get showProductAutologToggle() {
        return productModels.has(this.props.threadModel) && Boolean(this.props.threadId);
    },
    async toggleProductAutologs() {
        const thread = this.state.thread;
        if (!this.showProductAutologToggle || !thread || thread.status === "loading") {
            return;
        }
        this.state.hideProductAutologs = !hidden();
        browser.localStorage.setItem(preferenceKey, this.state.hideProductAutologs ? "1" : "0");
        this.state.reloadingProductAutologs = true;
        try {
            Object.assign(thread, {
                messages: [], pendingNewMessages: [], isLoaded: false,
                loadOlder: false, loadNewer: false, scrollTop: "bottom",
            });
            await this.threadService.fetchNewMessages(thread);
        } finally {
            this.state.reloadingProductAutologs = false;
        }
    },
});

patch(ThreadService.prototype, {
    getFetchRoute(thread) {
        if (isProductThread(thread)) {
            return "/product_vataga/mail/thread/messages";
        }
        return super.getFetchRoute(...arguments);
    },
    getFetchParams(thread) {
        const params = super.getFetchParams(...arguments);
        if (isProductThread(thread)) {
            params.hide_product_autologs = hidden();
        }
        return params;
    },
});
