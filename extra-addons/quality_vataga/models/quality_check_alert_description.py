from markupsafe import Markup

from odoo import _, models

from .measurement_utils import format_number


class QualityCheckAlertDescription(models.Model):
    _inherit = 'quality.check'

    def _get_technical_board_failure_description(self):
        """Safe HTML snapshot appended to the standard alert description."""
        self.ensure_one()
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
