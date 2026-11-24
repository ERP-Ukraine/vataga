/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { escape } from "@web/core/utils/strings";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";

// Мітка повідомлень між рамкою Ratel і цією сторінкою (як у panel/src/odoo/bridge.ts)
const MARK = "ratel-odoo:v1";
const TEAM_KEY = "ratel_support_embed.team";
const MAX_FILE = 20 * 1024 * 1024;
const MAX_DRAFT = 4000;

const isId = (v) => Number.isInteger(v) && v > 0;
const isText = (v, max) => typeof v === "string" && v.length <= max;

function toBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) {
        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(binary);
}

/**
 * Екран Ratel Support у рамці. Адресу з одноразовим підтвердженням особи видає сервер Odoo
 * на кожне відкриття. Маршрут — у контексті дії: `ratel_route` ("/", "/t/<id>", "/new").
 *
 * Зміст заявок розшифровує сторінка Ratel у рамці. До Odoo він потрапляє лише тоді, коли
 * фахівець там натискає «Створити квиток в Odoo»: рамка просить цю сторінку відкрити форму
 * квитка служби підтримки й дописати листування — усе від імені поточного користувача Odoo.
 * Запити приймаються тільки від самої рамки й тільки з адреси сервера Ratel.
 *
 * «Відповісти в застосунок» у квитку передає сюди лише текст-чернетку: шифрує, підписує й
 * надсилає його сторінка Ratel, коли фахівець там натисне «Надіслати».
 */
export class RatelSupportEmbed extends Component {
    static template = "ratel_support_embed.Embed";
    static props = ["*"];

    setup() {
        this.rpc = useService("rpc");
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.action = useService("action");
        this.frame = useRef("frame");
        this.origin = null;
        // Відповідь, написана у квитку Odoo: рамка забирає її один раз і показує як чернетку
        const draft = this.props.action?.context?.ratel_draft;
        this.draft = draft && isId(draft.ratel_id) && isText(draft.text, MAX_DRAFT) ? draft : null;
        this.state = useState({ src: null, error: null });
        const onMessage = (ev) => this.onMessage(ev);
        onWillStart(() => this.load());
        onMounted(() => browser.addEventListener("message", onMessage));
        onWillUnmount(() => browser.removeEventListener("message", onMessage));
    }

    async load() {
        this.state.src = null;
        this.state.error = null;
        this.origin = null;
        const route = this.props.action?.context?.ratel_route || "/";
        try {
            const { src } = await this.rpc("/ratel_support_embed/frame", { route }, { silent: true });
            this.origin = new URL(src).origin;
            this.state.src = src;
        } catch (e) {
            this.state.error = e?.data?.message || "Не вдалося відкрити Ratel Support.";
        }
    }

    async onMessage(ev) {
        const frame = this.frame.el;
        if (!frame || !this.origin || ev.origin !== this.origin || ev.source !== frame.contentWindow) {
            return;
        }
        const data = ev.data;
        if (!data || data.mark !== MARK || typeof data.id !== "string") {
            return;
        }
        let reply;
        try {
            const result = await this.handle(data.type, data.payload || {});
            reply = { ok: true, result };
        } catch (e) {
            reply = { ok: false, error: e?.data?.message || e?.message || "Помилка Odoo" };
        }
        ev.source.postMessage({ mark: MARK, id: data.id, ...reply }, this.origin);
    }

    handle(type, p) {
        switch (type) {
            case "lookup":
                return this.lookup(p);
            case "create":
                return this.create(p);
            case "file":
                return this.file(p);
            case "message":
                return this.message(p);
            case "open":
                return this.open(p);
            case "draft":
                return this.takeDraft(p);
        }
        throw new Error("Невідомий запит");
    }

    async lookup(p) {
        const found = await this.orm.call("helpdesk.ticket", "ratel_lookup", [p.number]);
        return found || null;
    }

    /** Звична форма нового квитка: команду, виконавця, пріоритет і мітки обирає людина. */
    create(p) {
        if (!isId(p.ratel_id) || !isText(p.number, 40) || !isText(p.title, 200) || !isText(p.description, 50000)) {
            throw new Error("Некоректні дані заявки");
        }
        const context = {
            default_name: p.title,
            // Текст із Ratel — лише текст: у поле з розміткою йде екранованим
            default_description: escape(p.description).replace(/\r?\n/g, "<br/>"),
            default_ratel_ticket_id: p.ratel_id,
            default_ratel_number: p.number,
        };
        if (["0", "1", "2", "3"].includes(p.priority)) {
            context.default_priority = p.priority;
        }
        const team = Number(browser.localStorage.getItem(TEAM_KEY));
        if (isId(team)) {
            context.default_team_id = team;
        }
        return new Promise((resolve) => {
            let saved = null;
            this.dialog.add(
                FormViewDialog,
                {
                    resModel: "helpdesk.ticket",
                    title: `Новий квиток із заявки ${p.number}`,
                    context,
                    onRecordSaved: (record) => {
                        saved = { id: record.resId, name: record.data.name || p.title };
                        const chosen = record.data.team_id;
                        if (Array.isArray(chosen) && isId(chosen[0])) {
                            browser.localStorage.setItem(TEAM_KEY, String(chosen[0]));
                        }
                    },
                },
                { onClose: () => resolve(saved) }
            );
        });
    }

    async file(p) {
        if (!isId(p.odoo_id) || !isText(p.name, 255) || !isText(p.mime, 100) || !(p.data instanceof ArrayBuffer)) {
            throw new Error("Некоректний файл");
        }
        if (p.data.byteLength > MAX_FILE) {
            throw new Error("Файл завеликий для Odoo");
        }
        const [id] = await this.orm.create("ir.attachment", [
            {
                name: p.name || "file",
                datas: toBase64(p.data),
                mimetype: p.mime,
                res_model: "helpdesk.ticket",
                res_id: p.odoo_id,
            },
        ]);
        return { id };
    }

    async message(p) {
        if (!isId(p.odoo_id) || !Array.isArray(p.attachment_ids) || !p.attachment_ids.every(isId)) {
            throw new Error("Некоректний запис");
        }
        return await this.orm.call("helpdesk.ticket", "ratel_add_message", [
            [p.odoo_id],
            p.key,
            p.header,
            p.text,
            p.attachment_ids,
        ]);
    }

    /** Чернетка відповіді для цієї заявки; надсилає її фахівець уже в екрані Ratel. */
    takeDraft(p) {
        const draft = this.draft;
        if (!draft || draft.ratel_id !== p.ratel_id) {
            return null;
        }
        this.draft = null;
        return { text: draft.text };
    }

    async open(p) {
        if (!isId(p.odoo_id)) {
            throw new Error("Некоректний квиток");
        }
        // Після відповіді рамці: перехід до квитка прибирає цей екран
        browser.setTimeout(() =>
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "helpdesk.ticket",
                res_id: p.odoo_id,
                views: [[false, "form"]],
                target: "current",
            })
        );
        return null;
    }
}

registry.category("actions").add("ratel_support_embed.embed", RatelSupportEmbed);
