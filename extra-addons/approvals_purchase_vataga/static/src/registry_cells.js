/** @odoo-module **/

import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { CharField, charField } from '@web/views/fields/char/char_field';
import { UrlField, urlField } from '@web/views/fields/url/url_field';
import { safePurchaseHref } from './url_policy';
import { BadgeField, badgeField } from '@web/views/fields/badge/badge_field';
import { _t } from '@web/core/l10n/translation';
import { Many2OneField, many2OneField } from '@web/views/fields/many2one/many2one_field';

export class RegistryStatusField extends BadgeField {
    get formattedValue() {
        return this.props.record.data[this.props.name] === 'cancel' ? _t('Скасовано') : super.formattedValue;
    }
}

export class RequestLinkField extends CharField {
    static template = 'approvals_purchase_vataga.RequestLink';

    setup() {
        super.setup();
        this.orm = useService('orm');
        this.action = useService('action');
    }

    async openRequest() {
        // Reuse the existing server action: it checks both line and parent ACLs,
        // record rules and active companies before returning the target ID.
        const action = await this.orm.call(this.props.record.resModel,
            'action_vataga_open_request', [[this.props.record.resId]]);
        await this.action.doAction(action);
    }
}

export class RegistryUrlField extends UrlField {
    static template = 'approvals_purchase_vataga.RegistryUrl';

    get formattedHref() {
        return safePurchaseHref(this.props.record.data[this.props.name]);
    }
}

export class PurchaseOrderLinkField extends Many2OneField {
    static template = 'approvals_purchase_vataga.PurchaseOrderLink';

    async openOrder() {
        const action = await this.orm.call(this.props.record.resModel,
            'action_vataga_open_purchase_order', [[this.props.record.resId]]);
        await this.action.doAction(action);
    }
}

registry.category('fields').add('vataga_request_link', { ...charField, component: RequestLinkField });
registry.category('fields').add('vataga_registry_url', { ...urlField, component: RegistryUrlField });
registry.category('fields').add('vataga_registry_status', { ...badgeField, component: RegistryStatusField });
registry.category('fields').add('vataga_purchase_order_link', { ...many2OneField, component: PurchaseOrderLinkField });
