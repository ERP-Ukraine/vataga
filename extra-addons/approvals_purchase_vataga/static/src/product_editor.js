/** @odoo-module **/

import { registry } from '@web/core/registry';
import { _t } from '@web/core/l10n/translation';
import { FormViewDialog } from '@web/views/view_dialogs/form_view_dialog';

// This dialog is used only by our wizard; native product forms are unpatched.
export class VatagaProductEditor extends FormViewDialog {
    setup() {
        super.setup();
        this.viewProps.buttonTemplate = 'approvals_purchase_vataga.ProductEditorButtons';
        this.viewProps.saveRecord = async (record) => {
            // Odoo 17 deliberately skips web_save for an unchanged existing
            // record. Explicit confirmation must still resolve that selection.
            const willSave = !record.resId || Object.keys(record._getChanges()).some((key) => key !== 'id');
            const saved = await record.save({ reload: false });
            if (!saved) {
                return false;
            }
            if (!willSave) {
                await this.env.services.orm.call('product.product', 'web_save', [[record.resId], {}], {
                    context: record.context, specification: {},
                });
            }
            this.props.close();
            await this.props.onRecordSaved(record);
            return true;
        };
    }
}

registry.category('actions').add('vataga_product_editor', (env, action) => {
    const params = action.params;
    env.services.dialog.add(VatagaProductEditor, {
        resModel: 'product.product', resId: params.res_id, viewId: params.view_id,
        context: params.context, title: _t('Створити та редагувати'), size: 'xl',
        onRecordSaved: () => env.services.action.doAction({ type: 'ir.actions.act_window_close' }),
    });
    // Function client actions leave the original ActionDialog mounted. Cancel,
    // X and Escape dismiss only this editor; the wizard keeps its local state.
});
