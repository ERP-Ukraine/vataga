/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";

import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";

const BOM_AUTOLOGS_HIDDEN_KEY = "mrp_vataga.hide_bom_autologs";

function areBomAutologsHidden() {
    return browser.localStorage.getItem(BOM_AUTOLOGS_HIDDEN_KEY) === "1";
}

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.hideBomAutologs = areBomAutologsHidden();
    },

    get showBomAutologToggle() {
        return this.props.threadModel === "mrp.bom" && Boolean(this.props.threadId);
    },

    get bomAutologsEnabled() {
        return this.showBomAutologToggle && !this.state.hideBomAutologs;
    },

    get bomAutologButtonLabel() {
        return this.bomAutologsEnabled ? "Автологи: вкл." : "Автологи: выкл.";
    },

    get bomAutologButtonIconClass() {
        return this.bomAutologsEnabled ? "fa fa-toggle-on" : "fa fa-toggle-off";
    },

    async toggleBomAutologs() {
        if (!this.showBomAutologToggle) {
            return;
        }
        this.state.hideBomAutologs = !this.state.hideBomAutologs;
        browser.localStorage.setItem(
            BOM_AUTOLOGS_HIDDEN_KEY,
            this.state.hideBomAutologs ? "1" : "0"
        );
        Object.assign(this.state.thread, {
            isLoaded: false,
            loadNewer: false,
            loadOlder: false,
            messages: [],
            pendingNewMessages: [],
            scrollTop: "bottom",
        });
        this.load(this.state.thread, ["messages"]);
    },
});

patch(ThreadService.prototype, {
    getFetchRoute(thread) {
        if (thread?.type === "chatter" && thread.model === "mrp.bom") {
            return "/mrp_vataga/mail/thread/messages";
        }
        return super.getFetchRoute(...arguments);
    },

    getFetchParams(thread) {
        const params = super.getFetchParams(...arguments);
        if (thread?.type === "chatter" && thread.model === "mrp.bom") {
            params.hide_bom_autologs = areBomAutologsHidden();
        }
        return params;
    },
});
