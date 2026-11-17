from odoo import api, models

from .product_autolog import related_audit, skipped


class ProductAutologLine(models.AbstractModel):
    _name = 'product.vataga.autolog.line'
    _description = 'Related Product Autolog'

    def _product_line_audit(self):
        return related_audit(
            self, self._product_audit_kind, self._product_audit_fields,
            type(self)._product_audit_owner, type(self)._product_audit_title,
            type(self)._product_audit_visible,
        )

    def _product_audit_owner(self):
        return self.product_tmpl_id

    def _product_audit_title(self):
        return self.display_name

    def _product_audit_visible(self):
        return True

    @api.model_create_multi
    def create(self, vals_list):
        if skipped(self):
            return super().create(vals_list)
        with self._product_line_audit() as audit:
            created = super(ProductAutologLine, audit['records']).create(vals_list)
            audit['records'] = created
        return created.with_env(self.env)

    def write(self, vals):
        if skipped(self) or not self:
            return super().write(vals)
        with self._product_line_audit() as audit:
            return super(ProductAutologLine, audit['records']).write(vals)

    def unlink(self):
        if skipped(self) or not self:
            return super().unlink()
        with self._product_line_audit() as audit:
            return super(ProductAutologLine, audit['records']).unlink()


class ProductPackaging(models.Model):
    _name = 'product.packaging'
    _inherit = ['product.packaging', 'product.vataga.autolog.line']

    _product_audit_kind = 'упаковку'
    _product_audit_fields = ('name', 'qty', 'barcode', 'sequence', 'company_id', 'sales', 'purchase',
                             'package_type_id', 'route_ids')

    def _product_audit_owner(self):
        return self.product_id


class ProductSupplierinfo(models.Model):
    _name = 'product.supplierinfo'
    _inherit = ['product.supplierinfo', 'product.vataga.autolog.line']

    _product_audit_kind = 'рядок постачальника'
    _product_audit_fields = ('partner_id', 'product_id', 'product_name', 'product_code', 'sequence',
                             'min_qty', 'price', 'discount', 'currency_id', 'company_id',
                             'date_start', 'date_end', 'delay')

    def _product_audit_owner(self):
        return self.product_id or self.product_tmpl_id


class ProductTemplateAttributeLine(models.Model):
    _name = 'product.template.attribute.line'
    _inherit = ['product.template.attribute.line', 'product.vataga.autolog.line']

    _product_audit_kind = 'атрибут'
    _product_audit_fields = ('attribute_id', 'value_ids', 'sequence')

    def _product_audit_title(self):
        return self.attribute_id.display_name

    def _product_audit_visible(self):
        return self.active


class ProductTemplateAttributeExclusion(models.Model):
    _name = 'product.template.attribute.exclusion'
    _inherit = ['product.template.attribute.exclusion', 'product.vataga.autolog.line']

    _product_audit_kind = 'виключення атрибутів'
    _product_audit_fields = ('product_template_attribute_value_id', 'value_ids')

    def _product_audit_owner(self):
        return self.product_template_attribute_value_id.product_tmpl_id

    def _product_audit_title(self):
        return self.product_template_attribute_value_id.display_name


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    def write(self, vals):
        if skipped(self) or 'price_extra' not in vals:
            return super().write(vals)
        with related_audit(
            self, 'значення атрибута', ('price_extra',),
            lambda value: value.product_tmpl_id, lambda value: value.display_name,
        ) as audit:
            return super(ProductTemplateAttributeValue, audit['records']).write(vals)
