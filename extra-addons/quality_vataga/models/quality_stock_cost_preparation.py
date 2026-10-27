"""Read-only accounting/time contract in preparation; not posting authority."""

from odoo import _, fields, models

from .stock_posting_core import access, production_sources


class QualityStockCostPreparation(models.Model):
    _inherit = 'mrp.production'

    def _stock_cost_preparation(self):
        self.ensure_one()
        self._check_unit_stock_release_access()
        product = self.product_id.with_company(self.company_id)
        moves = production_sources(self, 'stock.move', 'production_id = %s OR raw_material_production_id = %s')
        accounts = []
        for move in moves.filtered(lambda row: row.state not in ('done', 'cancel')):
            current = move.product_id.with_company(self.company_id)
            if current.valuation == 'real_time':
                journal, source, destination, asset = move._get_accounting_data_for_valuation()
                access(self.env['account.journal'].browse(journal))
                access(self.env['account.account'].browse([source, destination, asset]))
                accounts.append(dict(move=move.id, journal=journal, source=source, destination=destination, asset=asset))
        timers = self.env['quality.stock.cost.scope']._timers(self)
        bindings = self.env['quality.stock.timer.binding']._for_production(self)
        facts = []
        for timer in timers:
            binding = bindings.filtered(lambda row: row.timer_id == timer)
            batch = timer.quality_execution_batch_id
            access(batch); access(batch.execution_ids); access(binding)
            units = batch.execution_ids.controlled_unit_id if batch else binding.unit_ids
            access(units)
            employees = self.env['hr.employee'].browse() if 'hr.employee' in self.env else None
            for record in (timer, timer.workorder_id):
                for name in ('employee_id', 'employee_ids'):
                    if name in record._fields and employees is not None:
                        employees |= record[name]
            if employees is not None:
                access(employees)
            # No guessed salary, zero cost or work-center fallback for an
            # Enterprise employee interval. The native WO hook must first be
            # characterized in the installed Enterprise build, with HR ACLs.
            facts.append(dict(id=timer.id, workorder=timer.workorder_id.id,
                start=fields.Datetime.to_string(timer.date_start), end=fields.Datetime.to_string(timer.date_end),
                employee_costing=self.env['quality.stock.cost.scope']._employee_cost_mode(timer),
                employees=employees.ids if employees is not None else [],
                units=units.ids, binding=binding.id, batch=batch.id,
                missing_scope=not bool(units)))
        return dict(profile='standard_real_time_v1' if (product.cost_method, product.valuation) == ('standard', 'real_time') else 'legacy',
            method=product.cost_method, valuation=product.valuation, standard_unit_value=product.standard_price,
            accounting_routes=accounts, timers=facts, posting_available=False,
            employee_policy='native_enterprise_contract_required',
            variance_policy='actual_allocated_cost_minus_native_standard_svl; no_custom_gl_entry')

    def _get_unit_stock_release_proposal(self, permissions):
        proposal = super()._get_unit_stock_release_proposal(permissions)
        proposal['costing']['native_contract'] = self._stock_cost_preparation()
        return proposal

    def action_open_stock_timer_binding(self):
        self._check_unit_stock_release_access()
        return dict(type='ir.actions.act_window', res_model='quality.stock.timer.binding.wizard',
            name=_('Підтвердити склад спільного часу'), view_mode='form', target='new',
            views=[(self.env.ref('quality_vataga.stock_timer_binding_wizard_form').id, 'form')],
            context={'default_production_id': self.id})
