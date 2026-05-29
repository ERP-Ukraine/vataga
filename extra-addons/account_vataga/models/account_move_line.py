from odoo import _, api, fields, models


AUTOLOG_SKIP_CONTEXT_KEY = 'account_vataga_skip_move_autologs'


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    _tracking_ignored_fields = {
        'move_id',
        'company_id',
        'company_currency_id',
        'currency_id',
        'display_name',
        'epd_dirty',
        'compute_all_tax_dirty',
        'discount_allocation_dirty',
        'sequence',
        'write_date',
        'write_uid',
        '__last_update',
    }

    vataga_locked_analytic_plan_ids = fields.Json(
        compute='_compute_vataga_locked_analytic_plan_ids',
    )

    @api.depends('move_id.state')
    def _compute_vataga_locked_analytic_plan_ids(self):
        project_plan = self.env.ref(
            'analytic_vataga.account_analytic_plan_project',
            raise_if_not_found=False,
        )
        seller_contract_plan = self.env.ref(
            'analytic_vataga.account_analytic_plan_seller_contract',
            raise_if_not_found=False,
        )
        locked_plan_ids = [
            plan.id
            for plan in (project_plan, seller_contract_plan)
            if plan
        ]
        for line in self:
            line.vataga_locked_analytic_plan_ids = (
                locked_plan_ids if line.move_id.state != 'draft' else []
            )

    @api.depends(
        'account_id', 'partner_id', 'product_id',
        'move_id.project_account_id', 'move_id.budget_account_id',
        'move_id.cash_flow_item_account_id', 'move_id.seller_contract_id'
    )
    def _compute_analytic_distribution(self):
        for line in self:
            if line.display_type == 'product' or not line.move_id.is_invoice(include_receipts=True):
                set_analytic_accounts = [
                    str(account.id) for account in [
                        line.move_id.project_account_id,
                        line.move_id.budget_account_id,
                        line.move_id.cash_flow_item_account_id,
                        line.move_id.seller_contract_id
                    ] if account]
                if set_analytic_accounts:
                    ids_sts = ','.join(sorted(set_analytic_accounts))
                    line.analytic_distribution = {ids_sts: 100}
            else:
                super(AccountMoveLine, line)._compute_analytic_distribution()

    def _should_post_invoice_line_autolog(self):
        self.ensure_one()
        return (
            self.move_id
            and self.move_id.is_invoice(include_receipts=True)
            and self.display_type in (False, 'product')
        )

    def _tracked_invoice_line_fields(self, vals):
        fields_to_track = []
        for field_name in vals:
            if field_name.startswith('x_studio_'):
                continue
            if field_name in self._tracking_ignored_fields:
                continue
            field = self._fields.get(field_name)
            if not field or field.type in {'one2many'}:
                continue
            fields_to_track.append(field_name)
        return fields_to_track

    def _format_analytic_distribution(self):
        self.ensure_one()
        analytic_distribution = self.analytic_distribution or {}
        if not analytic_distribution:
            return _("Порожньо")
        account_ids = []
        for key in analytic_distribution:
            account_ids.extend(int(account_id) for account_id in key.split(',') if account_id)
        accounts = self.env['account.analytic.account'].browse(account_ids).exists()
        names_by_id = {account.id: account.display_name for account in accounts}
        parts = []
        for key, percentage in analytic_distribution.items():
            names = [
                names_by_id.get(int(account_id), account_id)
                for account_id in key.split(',')
                if account_id
            ]
            parts.append("%s: %s%%" % (", ".join(names), percentage))
        return "; ".join(parts)

    def _format_invoice_line_autolog_subject(self):
        self.ensure_one()
        return self.product_id.display_name or self.name or _("РџРѕСЂРѕР¶РЅСЊРѕ")

    def _format_tracked_value(self, field_name):
        self.ensure_one()
        if field_name == 'analytic_distribution':
            return self._format_analytic_distribution()
        field = self._fields[field_name]
        value = self[field_name]
        if field.type == 'many2one':
            return value.display_name or _("Порожньо")
        if field.type == 'many2many':
            return ", ".join(value.mapped('display_name')) or _("Порожньо")
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
        for line in lines.filtered(lambda line: line._should_post_invoice_line_autolog()):
            line.move_id._post_move_autolog(
                _("Додано рядок рахунку: %(product)s, кількість: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or line.name or _("Порожньо"),
                    'qty': line.quantity,
                    'uom': line.product_uom_id.display_name or _("Порожньо"),
                }
            )
        return lines

    def write(self, vals):
        tracked_fields = self._tracked_invoice_line_fields(vals)
        tracked_values = {
            line.id: {
                field_name: line._format_tracked_value(field_name)
                for field_name in tracked_fields
            }
            for line in self.filtered(lambda line: line._should_post_invoice_line_autolog())
        }
        res = super().write(vals)
        if self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY):
            return res
        for line in self.filtered(lambda line: line.id in tracked_values):
            changes = []
            for field_name in tracked_fields:
                old_value = tracked_values[line.id][field_name]
                new_value = line._format_tracked_value(field_name)
                if old_value == new_value:
                    continue
                field_label = line._fields[field_name].string or field_name
                changes.append(
                    _("%(field)s: %(old)s → %(new)s") % {
                        'field': field_label,
                        'old': old_value,
                        'new': new_value,
                    }
                )
            if changes:
                line.move_id._post_move_autolog(
                    _("Змінено рядок рахунку: %(product)s; %(changes)s") % {
                        'product': line._format_invoice_line_autolog_subject(),
                        'changes': '; '.join(changes),
                    }
                )
        return res

    def unlink(self):
        if self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY):
            return super().unlink()
        log_messages = [
            (
                line.move_id,
                _("Видалено рядок рахунку: %(product)s, кількість: %(qty)s %(uom)s") % {
                    'product': line.product_id.display_name or line.name or _("Порожньо"),
                    'qty': line.quantity,
                    'uom': line.product_uom_id.display_name or _("Порожньо"),
                },
            )
            for line in self.filtered(lambda line: line._should_post_invoice_line_autolog())
        ]
        res = super().unlink()
        for move, message in log_messages:
            move._post_move_autolog(body=message)
        return res
