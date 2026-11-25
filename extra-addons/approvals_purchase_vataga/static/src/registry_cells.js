/** @odoo-module **/

import { registry } from '@web/core/registry';
import { useService } from '@web/core/utils/hooks';
import { CharField, charField } from '@web/views/fields/char/char_field';
import { UrlField, urlField } from '@web/views/fields/url/url_field';
import { safePurchaseHref } from './url_policy';

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

registry.category('fields').add('vataga_request_link', { ...charField, component: RequestLinkField });
registry.category('fields').add('vataga_registry_url', { ...urlField, component: RegistryUrlField });
