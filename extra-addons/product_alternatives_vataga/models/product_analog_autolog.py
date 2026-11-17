from odoo import models
from odoo.addons.product_vataga.models.product_autolog import related_audit, skipped


class ProductAnalog(models.Model):
    _inherit = 'product.analog'

    def _skip_analog_autolog(self):
        return skipped(self) or self.env.context.get('product_alternatives_skip_reciprocal_sync')

    def _analog_audit(self):
        return related_audit(
            self, 'аналог', ('product_id', 'sequence'),
            lambda line: line.product_tmpl_id, lambda line: line.product_id.display_name,
            lambda line: line.is_primary_link,
        )

    def _create_primary_analog_line(self, vals):
        if self._skip_analog_autolog():
            return super()._create_primary_analog_line(vals)
        with self._analog_audit() as audit:
            created = super(ProductAnalog, audit['records'])._create_primary_analog_line(vals)
            audit['records'] = created
        return created.with_env(self.env)

    def write(self, vals):
        if self._skip_analog_autolog() or not {'product_tmpl_id', 'product_id', 'sequence', 'is_primary_link'} & vals.keys():
            return super().write(vals)
        with self._analog_audit() as audit:
            return super(ProductAnalog, audit['records']).write(vals)

    def unlink(self):
        if self._skip_analog_autolog():
            return super().unlink()
        with self._analog_audit() as audit:
            return super(ProductAnalog, audit['records']).unlink()
