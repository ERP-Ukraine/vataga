/** @odoo-module **/

import {
    AnalyticDistribution,
    analyticDistribution,
} from "@analytic/components/analytic_distribution/analytic_distribution";
import { patch } from "@web/core/utils/patch";

AnalyticDistribution.props = {
    ...AnalyticDistribution.props,
    vataga_locked_plan_field: { type: String, optional: true },
};

const originalExtractProps = analyticDistribution.extractProps;

analyticDistribution.extractProps = (params) => ({
    ...originalExtractProps(params),
    vataga_locked_plan_field: (params.options || {}).vataga_locked_plan_field,
});

patch(AnalyticDistribution.prototype, {
    get vatagaLockedPlanIds() {
        const fieldName = this.props.vataga_locked_plan_field;
        return fieldName ? this.props.record.data[fieldName] || [] : [];
    },

    isVatagaPlanLocked(planId) {
        return this.vatagaLockedPlanIds.includes(planId);
    },

    isVatagaAnalyticFieldLocked(fieldName) {
        const match = fieldName.match(/^x_plan(\d+)_id$/);
        return match ? this.isVatagaPlanLocked(Number(match[1])) : false;
    },

    recordProps(line) {
        const props = super.recordProps(...arguments);
        for (const account of line.analyticAccounts) {
            if (!this.isVatagaPlanLocked(account.planId)) {
                continue;
            }
            const fieldName = `x_plan${account.planId}_id`;
            if (props.fields[fieldName]) {
                props.fields[fieldName].readonly = true;
            }
            if (props.activeFields[fieldName]) {
                props.activeFields[fieldName].readonly = true;
            }
        }
        return props;
    },

    async lineChanged(record, changes, line) {
        const lockedAccounts = new Map(
            line.analyticAccounts
                .filter((account) => this.isVatagaPlanLocked(account.planId))
                .map((account) => [account.planId, { ...account }])
        );

        await super.lineChanged(...arguments);

        for (const account of line.analyticAccounts) {
            const previousAccount = lockedAccounts.get(account.planId);
            if (previousAccount) {
                Object.assign(account, previousAccount);
            }
        }
    },
});
