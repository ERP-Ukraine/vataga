import re

from odoo import _, api, models


DEFAULT_CODE_SEQUENCE_RE = re.compile(
    r'^([A-Za-z]{3})-([A-Za-z]{3})-(\d{4})(?:-.+)?$'
)
DEFAULT_CODE_MAX_SEQUENCE = 9999


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    @api.onchange('default_code')
    def _onchange_default_code(self):
        warning = super()._onchange_default_code()
        if warning and warning.get('warning') and warning['warning'].get('message'):
            self._add_next_default_code_to_warning(warning)
        return warning

    def _add_next_default_code_to_warning(self, warning):
        self.ensure_one()
        next_default_code = self._get_next_available_default_code(self.default_code)
        if next_default_code:
            warning['warning']['message'] = '%s %s' % (
                warning['warning']['message'],
                _('Next available internal reference: %s.') % next_default_code,
            )

    @api.model
    def _get_next_available_default_code(self, default_code):
        reference_parts = self._match_default_code_sequence(default_code)
        if not reference_parts:
            return False

        prefix, _number = reference_parts
        occupied_numbers = set()
        Product = self.env['product.product'].with_context(active_test=False).sudo()
        products = Product.search([('default_code', '=like', '%s-%%' % prefix)])
        for product in products:
            candidate_parts = self._match_default_code_sequence(product.default_code)
            if candidate_parts and candidate_parts[0] == prefix:
                occupied_numbers.add(candidate_parts[1])

        for number in range(1, DEFAULT_CODE_MAX_SEQUENCE + 1):
            if number not in occupied_numbers:
                return '%s-%04d' % (prefix, number)
        return False

    @api.model
    def _match_default_code_sequence(self, default_code):
        match = DEFAULT_CODE_SEQUENCE_RE.match(default_code or '')
        if not match:
            return False
        prefix = '%s-%s' % (match.group(1), match.group(2))
        return prefix, int(match.group(3))
