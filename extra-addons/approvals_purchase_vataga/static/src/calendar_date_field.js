/** @odoo-module **/

import { registry } from '@web/core/registry';
import { formatDate } from '@web/core/l10n/dates';
import { DateTimeField, dateTimeField } from '@web/views/fields/datetime/datetime_field';

// Date sources are projected at midnight UTC. Recover their calendar date
// before formatting, even when the client is west of UTC or crosses DST.
export function calendarDateText(value) {
    return value ? formatDate(value.toUTC()) : '';
}

export class VatagaCalendarDateField extends DateTimeField {
    getFormattedValue(valueIndex) {
        return calendarDateText(this.values[valueIndex]);
    }
}

registry.category('fields').add('vataga_calendar_date', {
    ...dateTimeField,
    component: VatagaCalendarDateField,
});
