from odoo import _, api, fields, models


AUTOLOG_SKIP_CONTEXT_KEY = 'mrp_vataga_skip_bom_autologs'


class MrpBom(models.Model):
    _inherit = 'mrp.bom'

    _autolog_tracked_fields = (
        'product_tmpl_id',
        'product_id',
        'product_qty',
        'product_uom_id',
        'type',
        'company_id',
    )

    def _should_skip_bom_autologs(self):
        return self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY)

    def _get_bom_autolog_fields(self, vals):
        return [field_name for field_name in self._autolog_tracked_fields if field_name in vals]

    def _format_bom_autolog_value(self, field_name):
        self.ensure_one()
        field = self._fields[field_name]
        value = self[field_name]

        if field.type == 'many2one':
            return value.display_name or _("Порожньо")
        if field.type == 'selection':
            selection = field._description_selection(self.env)
            return dict(selection).get(value, value or _("Порожньо"))
        if field.type == 'boolean':
            return _("Так") if value else _("Ні")
        if field.type == 'date':
            return fields.Date.to_string(value) if value else _("Порожньо")
        if field.type == 'datetime':
            return fields.Datetime.to_string(value) if value else _("Порожньо")
        if value in (False, None, ''):
            return _("Порожньо")
        return str(value)

    def _post_bom_autolog(self, body):
        self.ensure_one()
        if self._should_skip_bom_autologs():
            return self.env['mail.message']
        return self.message_post(
            body=body,
            subtype_xmlid='mrp_vataga.mt_bom_autolog',
        )

    def write(self, vals):
        tracked_fields = self._get_bom_autolog_fields(vals)
        tracked_values = {
            bom.id: {
                field_name: bom._format_bom_autolog_value(field_name)
                for field_name in tracked_fields
            }
            for bom in self
        }
        res = super().write(vals)
        if self._should_skip_bom_autologs():
            return res
        for bom in self:
            changes = []
            for field_name in tracked_fields:
                old_value = tracked_values[bom.id][field_name]
                new_value = bom._format_bom_autolog_value(field_name)
                if old_value == new_value:
                    continue
                field_label = bom._fields[field_name].string or field_name
                changes.append(
                    _("%(field)s: %(old)s -> %(new)s") % {
                        'field': field_label,
                        'old': old_value,
                        'new': new_value,
                    }
                )
            if changes:
                bom._post_bom_autolog(
                    _("Специфікацію змінено: %(changes)s") % {
                        'changes': '; '.join(changes),
                    }
                )
        return res

    def copy(self, default=None):
        return super(
            MrpBom, self.with_context(**{AUTOLOG_SKIP_CONTEXT_KEY: True})
        ).copy(default=default)


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    _tracking_ignored_fields = {
        'bom_id',
        'company_id',
        'display_name',
        'sequence',
        'write_date',
        'write_uid',
        '__last_update',
    }

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
            return value.display_name or _("Порожньо")
        if field.type == 'selection':
            selection = field._description_selection(self.env)
            return dict(selection).get(value, value or _("Порожньо"))
        if field.type == 'boolean':
            return _("Так") if value else _("Ні")
        if field.type == 'date':
            return fields.Date.to_string(value) if value else _("Порожньо")
        if field.type == 'datetime':
            return fields.Datetime.to_string(value) if value else _("Порожньо")
        if value in (False, None, ''):
            return _("Порожньо")
        return str(value)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY):
            return lines
        for line in lines.filtered('bom_id'):
            line.bom_id._post_bom_autolog(
                _("Додано компонент: %(product)s, кількість: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or _("Порожньо"),
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.display_name or _("Порожньо"),
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
        if self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY):
            return res
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
                line.bom_id._post_bom_autolog(
                    _("Змінено компонент: %(changes)s") % {
                        'changes': '; '.join(changes),
                    }
                )
        return res

    def unlink(self):
        if self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY):
            return super().unlink()
        log_messages = [
            (
                line.bom_id,
                _("Видалено компонент: %(product)s, кількість: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or _("Порожньо"),
                    'qty': line.product_qty,
                    'uom': line.product_uom_id.display_name or _("Порожньо"),
                },
            )
            for line in self.filtered('bom_id')
        ]
        res = super().unlink()
        for bom, message in log_messages:
            bom._post_bom_autolog(body=message)
        return res
