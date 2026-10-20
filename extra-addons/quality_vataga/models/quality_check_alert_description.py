from markupsafe import Markup

from odoo import _, models

from .measurement_utils import format_number


class QualityCheckAlertDescription(models.Model):
    _inherit = 'quality.check'

    def _get_current_matrix_failure_data(self):
        """Independent data contract for a future verified sorting-field adapter.

        Values and norms come from the check snapshot, never the current QCP.
        Reading this method cannot change a check, alert or board decision.
        """
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        failures = []
        for sample in self.sample_ids.filtered(lambda row: row.sample_result != 'pass'):
            if sample.visual_result == 'no':
                failures.append({
                    'sample_number': sample.sample_number,
                    'parameter_name': _('візуальний контроль'),
                    'actual_value': _('Ні'),
                    'expected_value': _('Так'),
                    'failure_reason': _('Візуальний контроль не пройдено.'),
                })
            for value in sample.measurement_value_ids.filtered(lambda cell: cell.result == 'fail'):
                column = value.column_id
                if value.parameter_type == 'numeric':
                    unit = column.parameter_unit or ''
                    actual = ('%s %s' % (format_number(value.numeric_value), unit)).strip()
                    expected = ('%s %s' % (column._get_tolerance_label() or '', unit)).strip()
                elif value.parameter_type == 'boolean':
                    actual = _('Так') if value.boolean_value == 'yes' else _('Ні')
                    expected = column.text_norm
                else:
                    actual = value.string_value or ''
                    expected = column.text_norm
                failures.append({
                    'sample_number': sample.sample_number,
                    'parameter_name': column.parameter_name,
                    'actual_value': actual,
                    'expected_value': expected or _('Норму не задано'),
                    'failure_reason': value.failure_reason or _('Параметр не відповідає нормі.'),
                })
        return failures

    def _format_matrix_failure_data(self, failures, as_html=False):
        lines = [_(
            'Зразок №%(sample_number)s — %(parameter_name)s: '
            'фактично «%(actual_value)s»; норма: %(expected_value)s. '
            'Причина: %(failure_reason)s',
            **failure,
        ) for failure in failures]
        if not lines:
            return ''
        if not as_html:
            return '\n'.join(lines)
        return Markup('<p>%s</p><ul>%s</ul>') % (
            _('Виявлені невідповідності'),
            Markup('').join(Markup('<li>%s</li>') % line for line in lines),
        )

    def _get_technical_board_failure_description(self):
        """Safe HTML snapshot appended to the standard alert description."""
        self.ensure_one()
        if self.x_control_stage == 'in_process':
            return self._format_matrix_failure_data(
                self._get_current_matrix_failure_data(), as_html=True,
            )
        items = []
        for sample in self.sample_ids:
            if sample.visual_result == 'no':
                items.append(_(
                    '%(sample)s: візуальний контроль — невідповідність.',
                    sample=sample.display_name,
                ))
            for value in sample.measurement_value_ids.filtered(lambda item: item.result == 'fail'):
                if value.parameter_type == 'numeric':
                    actual = format_number(value.numeric_value)
                elif value.parameter_type == 'boolean':
                    actual = _('Так') if value.boolean_value == 'yes' else _('Ні')
                else:
                    actual = value.string_value or ''
                items.append(_(
                    '%(sample)s — %(parameter)s: %(actual)s %(unit)s. %(reason)s',
                    sample=sample.display_name,
                    parameter=value.column_id.parameter_name,
                    actual=actual,
                    unit=value.column_id.parameter_unit or '',
                    reason=value.failure_reason or '',
                ))
        if not items:
            return ''
        return Markup('<p>%s</p><ul>%s</ul>') % (
            _('Виявлені невідповідності'),
            Markup('').join(Markup('<li>%s</li>') % item for item in items),
        )
