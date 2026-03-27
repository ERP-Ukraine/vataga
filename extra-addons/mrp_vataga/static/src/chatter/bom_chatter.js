/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

const BOM_AUTOLOGS_HIDDEN_KEY = "mrp_vataga.hide_bom_autologs";

function areBomAutologsHidden() {
    return browser.localStorage.getItem(BOM_AUTOLOGS_HIDDEN_KEY) === "1";
}

patch(Chatter.prototype, {
    get showBomAutologToggle() {
        return this.props.threadModel === "mrp.bom" && Boolean(this.props.threadId);
    },

    get bomAutologsEnabled() {
        return this.showBomAutologToggle && !areBomAutologsHidden();
    },

    get bomAutologButtonLabel() {
        return this.bomAutologsEnabled ? _t("Hide autologs") : _t("Show autologs");
    },

    get bomAutologButtonIconClass() {
        return this.bomAutologsEnabled ? "fa fa-eye-slash" : "fa fa-eye";
    },

    async toggleBomAutologs() {
        if (!this.showBomAutologToggle) {
            return;
        }
        browser.localStorage.setItem(
            BOM_AUTOLOGS_HIDDEN_KEY,
            this.bomAutologsEnabled ? "1" : "0"
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
