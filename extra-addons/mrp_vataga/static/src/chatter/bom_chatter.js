/** @odoo-module */

import { Chatter } from "@mail/core/web/chatter";
import { ThreadService } from "@mail/core/common/thread_service";

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

patch(Chatter.prototype, {
    get showBomAutologToggle() {
        return this.props.threadModel === "mrp.bom" && Boolean(this.props.threadId);
    },

    get bomAutologsEnabled() {
        return this.showBomAutologToggle && this.props.webRecord?.data?.bom_autologs_enabled !== false;
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
        await this.orm.write("mrp.bom", [this.props.threadId], {
            bom_autologs_enabled: !this.bomAutologsEnabled,
        });
        await this.props.webRecord?.load();
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
});
