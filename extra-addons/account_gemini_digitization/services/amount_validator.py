class AmountValidator:
    def __init__(self, env):
        self.env = env

    def validate_job(self, job):
        return {
            'is_valid': True,
            'warnings': [],
            'errors': [],
        }

    def validate_move_totals(self, move, job):
        warnings = []
        currency = move.currency_id or job.currency_id
        comparisons = (
            ('Untaxed amount', move.amount_untaxed, job.recognized_amount_untaxed),
            ('Tax amount', move.amount_tax, job.recognized_amount_tax),
            ('Total amount', move.amount_total, job.recognized_amount_total),
        )
        for label, document_value, recognized_value in comparisons:
            if not self._is_meaningful_amount(recognized_value):
                continue
            if not self._amounts_close(currency, document_value, recognized_value):
                warnings.append(
                    '%s differs after apply: document=%s, recognized=%s.'
                    % (label, document_value, recognized_value)
                )
        return warnings

    def _is_meaningful_amount(self, value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value != 0

    def _amounts_close(self, currency, document_value, recognized_value):
        difference = abs((document_value or 0.0) - (recognized_value or 0.0))
        tolerance = max(getattr(currency, 'rounding', 0.01) * 2, 0.05)
        return difference <= tolerance
