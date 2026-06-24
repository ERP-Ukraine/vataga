class DigitizationApplyService:
    """Apply reviewed persistent OCR lines through the shared validation flow."""

    def __init__(self, env, job):
        self.env = env
        self.job = job

    def apply(self):
        self.job.ensure_one()
        apply_model = self.env['account.gemini.digitization.review.wizard']
        return _JobApplyContext(self.job, apply_model).action_apply()


class _JobApplyContext:
    """Expose a job and its persistent lines to the shared Apply methods."""

    def __init__(self, job, apply_model):
        self.job = job
        self.apply_model = apply_model
        self.env = job.env

    @property
    def job_id(self):
        return self.job

    def ensure_one(self):
        self.job.ensure_one()
        return self

    def _is_line_in_apply_context(self, line):
        return bool(line and line.job_id == self.job)

    def _has_manual_merge_values(self, line):
        original_price = self._first_number(
            line.price_unit_without_tax,
            line.price_unit_with_tax,
        )
        original_subtotal = self._line_subtotal(line)
        original_quantity = False
        if self._is_positive_number(original_price) and self._is_number(
            original_subtotal
        ):
            original_quantity = original_subtotal / original_price

        current_quantity = self._to_float(line.quantity)
        current_price = self._to_float(line.price_unit)
        quantity_changed = (
            self._is_number(original_quantity)
            and self._is_number(current_quantity)
            and not self._numbers_close(
                original_quantity,
                current_quantity,
                tolerance=0.0001,
            )
        )
        price_changed = (
            self._is_number(original_price)
            and self._is_number(current_price)
            and not self._numbers_close(original_price, current_price, tolerance=0.01)
        )
        return quantity_changed or price_changed

    def __getattr__(self, name):
        method = getattr(type(self.apply_model), name, None)
        if callable(method):
            return method.__get__(self, type(self))
        return getattr(self.job, name)
