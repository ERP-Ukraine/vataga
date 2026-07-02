/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

patch(FormController.prototype, {
    async beforeExecuteActionButton(clickParams) {
        if (
            this.model.root.resModel === "account.move" &&
            clickParams.name === "button_draft" &&
            this.model.root.data.has_checked_moderation_fields
        ) {
            const confirmed = await new Promise((resolve) => {
                this.dialogService.add(ConfirmationDialog, {
                    title: _t("Підтвердження"),
                    body: _t(
                        "Ви впевнені, що хочете перейти в чернетку? Кнопки модерації будуть скинуті."
                    ),
                    confirmLabel: _t("Гаразд"),
                    cancelLabel: _t("Скасувати"),
                    confirm: () => resolve(true),
                    cancel: () => resolve(false),
                });
            });
            if (!confirmed) {
                return false;
            }
        }
        return super.beforeExecuteActionButton(...arguments);
    },
});
