import logging

from odoo import _, fields, models


_logger = logging.getLogger(__name__)

AUTOLOG_SKIP_CONTEXT_KEY = 'account_vataga_skip_move_autologs'


class AccountMove(models.Model):
    _inherit = 'account.move'

    ANALYTIC_HEADER_FIELDS = {
        'project_account_id',
        'budget_account_id',
        'cash_flow_item_account_id',
        'seller_contract_id',
    }

    _autolog_tracked_fields = (
        'partner_id',
        'invoice_date',
        'invoice_date_due',
        'ref',
        'project_account_id',
        'budget_account_id',
        'cash_flow_item_account_id',
        'seller_contract_id',
    )

    project_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_project', '=', True)]"
    )
    budget_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_budget', '=', True)]"
    )
    cash_flow_item_account_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_cash_flow_item', '=', True)]"
    )
    seller_contract_id = fields.Many2one(
        'account.analytic.account', domain="[('is_plan_seller_contract', '=', True)]"
    )
    has_checked_moderation_fields = fields.Boolean(
        compute='_compute_has_checked_moderation_fields',
    )

    def _get_studio_boolean_fields_to_reset(self):
        fields_to_reset = self.env['ir.model.fields'].sudo().search([
            ('model', '=', 'account.move'),
            ('ttype', '=', 'boolean'),
            ('state', '=', 'manual'),
            ('name', '=like', 'x_studio_%'),
        ])
        return [
            field.name
            for field in fields_to_reset
            if field.name in self._fields
        ]

    def _compute_has_checked_moderation_fields(self):
        fields_to_reset = self._get_studio_boolean_fields_to_reset()
        for move in self:
            move.has_checked_moderation_fields = any(
                move[field_name]
                for field_name in fields_to_reset
            )

    def _should_skip_move_autologs(self):
        return self.env.context.get(AUTOLOG_SKIP_CONTEXT_KEY)

    def _get_move_autolog_fields(self, vals):
        return [
            field_name
            for field_name in self._autolog_tracked_fields
            if field_name in vals and not field_name.startswith('x_studio_')
        ]

    def _format_move_autolog_value(self, field_name):
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

    def _post_move_autolog(self, body):
        self.ensure_one()
        if self._should_skip_move_autologs():
            return self.env['mail.message']
        return self.message_post(
            body=body,
            subtype_xmlid='account_vataga.mt_account_move_autolog',
        )

    def write(self, vals):
        tracked_fields = self._get_move_autolog_fields(vals)
        tracked_values = {
            move.id: {
                field_name: move._format_move_autolog_value(field_name)
                for field_name in tracked_fields
            }
            for move in self.filtered(lambda move: move.is_invoice(include_receipts=True))
        }
        posted_invoice_lines = self.env['account.move.line']
        if set(vals) & self.ANALYTIC_HEADER_FIELDS:
            posted_invoice_lines = self.filtered(
                lambda move: move.state == 'posted' and move.is_invoice(include_receipts=True)
            ).invoice_line_ids

        res = super().write(vals)

        if posted_invoice_lines:
            posted_invoice_lines._inverse_analytic_distribution()

        if not self._should_skip_move_autologs():
            for move in self.filtered(lambda move: move.id in tracked_values):
                changes = []
                for field_name in tracked_fields:
                    old_value = tracked_values[move.id][field_name]
                    new_value = move._format_move_autolog_value(field_name)
                    if old_value == new_value:
                        continue
                    field_label = move._fields[field_name].string or field_name
                    changes.append(
                        _("%(field)s: %(old)s → %(new)s") % {
                            'field': field_label,
                            'old': old_value,
                            'new': new_value,
                        }
                    )
                if changes:
                    move._post_move_autolog(
                        _("Рахунок змінено: %(changes)s") % {
                            'changes': '; '.join(changes),
                        }
                    )

        return res

    def button_draft(self):
        posted_invoice_moves = self.filtered(
            lambda move: move.state == 'posted' and move.move_type == 'in_invoice'
        )

        res = super().button_draft()

        fields_to_reset = self._get_studio_boolean_fields_to_reset()
        if posted_invoice_moves and fields_to_reset:
            _logger.info("Resetting Studio boolean fields on posted vendor bills: %s", fields_to_reset)
            posted_invoice_moves.write({
                field_name: False
                for field_name in fields_to_reset
            })

        return res
