/** @odoo-module **/

export function safePurchaseHref(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value) || /[\s\\\x00-\x1f\x7f]/.test(value)) {
        return undefined;
    }
    try {
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
            return undefined;
        }
        return value;
    } catch {
        return undefined;
    }
}
