from markupsafe import Markup

from odoo import _, models

class QualityCheckAlertDescription(models.Model):
    _inherit = 'quality.check'

    def _get_technical_board_failure_description(self):
        """Safe HTML snapshot appended to the standard alert description."""
        self.ensure_one()
        items = []
        for sample in self.sample_ids:
            current = sample._measurement_snapshot()
            for key, detail in (sample.initial_failures or {}).items():
                if not current.get(key, {}).get('reason') or current[key]['value'] != detail['value']:
                    items.append(_(
                        '%(sample)s — початкова невідповідність «%(parameter)s»: %(actual)s. %(reason)s',
                        sample=sample.display_name, parameter=detail['name'],
                        actual=detail['display'], reason=detail['reason'],
                    ))
            for detail in current.values():
                if detail['reason']:
                    items.append(_(
                        '%(sample)s — %(parameter)s: %(actual)s. %(reason)s',
                        sample=sample.display_name, parameter=detail['name'],
                        actual=detail['display'], reason=detail['reason'],
                    ))
        if not items:
            return ''
        return Markup('<p>%s</p><ul>%s</ul>') % (
            _('Виявлені невідповідності'),
            Markup('').join(Markup('<li>%s</li>') % item for item in items),
        )
