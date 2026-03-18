from odoo import _, api, fields, models


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    product_tmpl_id = fields.Many2one('product.template', tracking=True)
    product_id = fields.Many2one('product.product', tracking=True)
    product_qty = fields.Float(tracking=True)
    product_uom_id = fields.Many2one('uom.uom', tracking=True)
    type = fields.Selection(tracking=True)
    company_id = fields.Many2one('res.company', tracking=True)


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    _tracking_ignored_fields = {'write_date', 'write_uid', '__last_update', 'display_name'}

    def _tracked_component_fields(self, vals):
        fields_to_track = []
        for field_name in vals:
            if field_name in self._tracking_ignored_fields:
                continue
            field = self._fields.get(field_name)
            if not field or field.type in {'one2many', 'many2many'}:
                continue
            fields_to_track.append(field_name)
        return fields_to_track

    def _format_tracked_value(self, field_name):
        self.ensure_one()
        field = self._fields[field_name]
        value = self[field_name]

        if field.type == 'many2one':
            return value.display_name or _("Empty")
        if field.type == 'selection':
            selection = field._description_selection(self.env)
            return dict(selection).get(value, value or _("Empty"))
        if field.type == 'boolean':
            return _("Yes") if value else _("No")
        if field.type == 'date':
            return fields.Date.to_string(value) if value else _("Empty")
        if field.type == 'datetime':
            return fields.Datetime.to_string(value) if value else _("Empty")
        if value in (False, None, ''):
            return _("Empty")
        return str(value)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines.filtered('bom_id'):
            line.bom_id.message_post(
                body=_("Component added: %(product)s, quantity: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or _("Empty"),
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.display_name or _("Empty"),
                }
            )
        return lines

    def write(self, vals):
        tracked_fields = self._tracked_component_fields(vals)
        tracked_values = {
            line.id: {
                field_name: line._format_tracked_value(field_name)
                for field_name in tracked_fields
            }
            for line in self
        }
        res = super().write(vals)
        for line in self.filtered('bom_id'):
            changes = []
            for field_name in tracked_fields:
                old_value = tracked_values[line.id][field_name]
                new_value = line._format_tracked_value(field_name)
                if old_value == new_value:
                    continue
                field_label = line._fields[field_name].string or field_name
                changes.append(
                    _("%(field)s: %(old)s -> %(new)s") % {
                        'field': field_label,
                        'old': old_value,
                        'new': new_value,
                    }
                )
            if changes:
                line.bom_id.message_post(
                    body=_("Component updated: %(component)s (%(changes)s)") % {
                        'component': line.product_id.display_name or _("Empty"),
                        'changes': '; '.join(changes),
                    }
                )
        return res

    def unlink(self):
        log_messages = [
            (
                line.bom_id,
                _("Component removed: %(product)s, quantity: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or _("Empty"),
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.display_name or _("Empty"),
                },
            )
            for line in self.filtered('bom_id')
        ]
        res = super().unlink()
        for bom, message in log_messages:
            bom.message_post(body=message)
        return res
