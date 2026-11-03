import hashlib
import json

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .measurement_utils import format_number


class QualityCheckAlertDescription(models.Model):
    _inherit = 'quality.check'

    can_create_matrix_quality_alert = fields.Boolean(
        string='Можна створити сповіщення про невідповідності',
        compute='_compute_can_create_matrix_quality_alert',
    )

    @api.depends(
        'x_control_stage', 'sample_ids', 'sample_ids.sample_result',
        'sample_ids.visual_result', 'sample_ids.measurement_value_ids.result',
    )
    def _compute_can_create_matrix_quality_alert(self):
        for check in self:
            check.can_create_matrix_quality_alert = bool(
                check.x_control_stage == 'in_process'
                and check.sample_ids
                and check._get_current_matrix_failure_data()
            )

    def do_alert(self):
        self.ensure_one()
        if self.x_control_stage == 'in_process':
            return self.action_open_matrix_quality_alert()
        return super().do_alert()

    def action_open_matrix_quality_alert(self):
        self.ensure_one()
        self.check_access_rights('read')
        self.check_access_rule('read')
        alert_model = self.env['quality.alert']
        alert_model.check_access_rights('create')
        if self.x_control_stage != 'in_process' or not self._get_current_matrix_failure_data():
            raise UserError(_(
                'Створити сповіщення цією дією можна лише для міжопераційної '
                'перевірки з актуальними невідповідностями матриці.',
            ))

        # An object button can return an inline action. Reading a stored
        # ir.actions.act_window unnecessarily requires access to its metadata.
        # The alert itself still uses the inspector's normal ACLs and rules.
        key = self._matrix_alert_request_key()
        existing = alert_model.search([
            ('check_id', '=', self.id), ('vataga_matrix_alert_key', '=', key),
        ], limit=1)
        view = self.env.ref('quality_control.quality_alert_view_form')
        context = {
            key: value for key, value in self.env.context.items()
            if not key.startswith('default_')
        }
        sources = {'check_id': self}
        for name in (
            'product_id', 'team_id', 'production_id', 'company_id',
            'picking_id', 'lot_id', 'partner_id', 'workorder_id', 'workcenter_id',
        ):
            source_field = self._fields.get(name)
            if source_field and source_field.type == 'many2one':
                sources[name] = self[name]
        if self.product_id:
            sources['product_tmpl_id'] = self.product_id.product_tmpl_id
        if 'workcenter_id' not in sources and 'workorder_id' in sources:
            sources['workcenter_id'] = sources['workorder_id'].workcenter_id
        for name, record in sources.items():
            target_field = alert_model._fields.get(name)
            if (
                target_field and target_field.type == 'many2one'
                and target_field.comodel_name == record._name
            ):
                context['default_' + name] = record.id or False
        if context.get('default_check_id') != self.id:
            raise UserError(_('У моделі сповіщення немає стандартного зв’язку з перевіркою.'))
        context.update(active_model=self._name, active_id=self.id, active_ids=self.ids,
                       quality_matrix_alert=True)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Створити сповіщення'),
            'res_model': 'quality.alert',
            'view_mode': 'form',
            'view_id': view.id,
            'views': [(view.id, 'form')],
            'res_id': existing.id or False,
            'target': 'current',
            'context': context,
        }

    def _matrix_alert_request_key(self):
        self.ensure_one()
        # Hash immutable row IDs and raw evidence, not translated descriptions.
        # The same request must stay the same when the inspector changes language.
        failures = [
            (sample.id, sample.visual_result, [
                (value.id, value.numeric_value, value.boolean_value, value.string_value)
                for value in sample.measurement_value_ids.filtered(
                    lambda cell: cell.result == 'fail').sorted('id')
            ])
            for sample in self.sample_ids.filtered(
                lambda row: row.visual_result == 'no'
                or any(cell.result == 'fail' for cell in row.measurement_value_ids)
            ).sorted('id')
        ]
        return hashlib.sha256(json.dumps(
            failures, ensure_ascii=False, sort_keys=True,
        ).encode('utf-8')).hexdigest()

    def _get_current_matrix_failure_data(self):
        """Shared current-failure data for alert fields and descriptions.

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
