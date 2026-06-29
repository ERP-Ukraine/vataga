/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";

import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";

const ACCOUNT_MOVE_AUTOLOGS_HIDDEN_KEY = "account_vataga.hide_account_move_autologs";

function areAccountMoveAutologsHidden() {
    return browser.localStorage.getItem(ACCOUNT_MOVE_AUTOLOGS_HIDDEN_KEY) === "1";
}

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.state.hideAccountMoveAutologs = areAccountMoveAutologsHidden();
    },

    get showAccountMoveAutologToggle() {
        return this.props.threadModel === "account.move" && Boolean(this.props.threadId);
    },

    get accountMoveAutologsEnabled() {
        return this.showAccountMoveAutologToggle && !this.state.hideAccountMoveAutologs;
    },

    get accountMoveAutologButtonLabel() {
        return "Автологи";
    },

    get accountMoveAutologButtonIconClass() {
        return this.accountMoveAutologsEnabled ? "fa fa-toggle-on" : "fa fa-toggle-off";
    },

    async toggleAccountMoveAutologs() {
        if (!this.showAccountMoveAutologToggle) {
            return;
        }
        this.state.hideAccountMoveAutologs = !this.state.hideAccountMoveAutologs;
        browser.localStorage.setItem(
            ACCOUNT_MOVE_AUTOLOGS_HIDDEN_KEY,
            this.state.hideAccountMoveAutologs ? "1" : "0"
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
        if (thread?.type === "chatter" && thread.model === "account.move") {
            return "/account_vataga/mail/thread/messages";
        }
        return super.getFetchRoute(...arguments);
    },

    getFetchParams(thread) {
        const params = super.getFetchParams(...arguments);
        if (thread?.type === "chatter" && thread.model === "account.move") {
            params.hide_account_move_autologs = areAccountMoveAutologsHidden();
        }
        return params;
    },
});
