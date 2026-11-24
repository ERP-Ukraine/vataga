/** @odoo-module **/

import { registry } from '@web/core/registry';
import { UrlField, urlField, formUrlField } from '@web/views/fields/url/url_field';
import { safePurchaseHref } from './url_policy';

class PurchaseUrlField extends UrlField {
    get formattedHref() {
        return safePurchaseHref(this.props.record.data[this.props.name]);
    }
}

class FormPurchaseUrlField extends PurchaseUrlField {
    static template = 'web.FormUrlField';
}

registry.category('fields').add('vataga_purchase_url', { ...urlField, component: PurchaseUrlField });
registry.category('fields').add('form.vataga_purchase_url', { ...formUrlField, component: FormPurchaseUrlField });
