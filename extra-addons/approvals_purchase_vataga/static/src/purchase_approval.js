/** @odoo-module **/

import { Component } from '@odoo/owl';
import { registry } from '@web/core/registry';
import { _t } from '@web/core/l10n/translation';
import { useService } from '@web/core/utils/hooks';
import { standardFieldProps } from '@web/views/fields/standard_field_props';
import { FormController } from '@web/views/form/form_controller';
import { formView } from '@web/views/form/form_view';

export class VatagaPurchaseController extends FormController {
    getStaticActionMenuItems() {
        return {
            ...super.getStaticActionMenuItems(),
            vatagaDuplicateWithApproval: {
                sequence: 31, icon: 'fa fa-clone', description: _t('Дублювати з погодженням'),
                isAvailable: () => !!this.model.root.resId && this.model.root.data.vataga_can_duplicate_with_approval,
                callback: async () => {
                    const action = await this.orm.call('purchase.order', 'action_vataga_duplicate_with_approval',
                        [[this.model.root.resId]]);
                    await this.actionService.doAction(action);
                },
            },
        };
    }
}

export class ApprovalDocuments extends Component {
    static template = 'approvals_purchase_vataga.ApprovalDocuments';
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
    }

    async openApproval(requestId) {
        const action = await this.orm.call('purchase.order', 'action_vataga_open_approval',
            [[this.props.record.resId], requestId]);
        await this.action.doAction(action);
    }
}

registry.category('views').add('vataga_purchase_order_form', { ...formView, Controller: VatagaPurchaseController });
registry.category('fields').add('vataga_approval_documents', {
    component: ApprovalDocuments, supportedTypes: ['many2many'],
    relatedFields: () => [{ name: 'display_name', type: 'char' }, { name: 'vataga_display_number', type: 'char' }],
});
