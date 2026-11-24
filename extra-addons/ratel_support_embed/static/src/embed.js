/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Екран Ratel Support у рамці. Адресу з одноразовим підтвердженням особи видає сервер Odoo
 * на кожне відкриття; зміст заявок розшифровує сторінка Ratel у рамці — до Odoo він не потрапляє.
 * Маршрут — у контексті дії: `ratel_route` ("/", "/t/<id>", "/new").
 */
export class RatelSupportEmbed extends Component {
    static template = "ratel_support_embed.Embed";
    static props = ["*"];

    setup() {
        this.rpc = useService("rpc");
        this.state = useState({ src: null, error: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.src = null;
        this.state.error = null;
        const route = this.props.action?.context?.ratel_route || "/";
        try {
            const { src } = await this.rpc("/ratel_support_embed/frame", { route }, { silent: true });
            this.state.src = src;
        } catch (e) {
            this.state.error = e?.data?.message || "Не вдалося відкрити Ratel Support.";
        }
    }
}

registry.category("actions").add("ratel_support_embed.embed", RatelSupportEmbed);
