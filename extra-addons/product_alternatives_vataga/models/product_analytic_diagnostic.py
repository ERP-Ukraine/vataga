from odoo import api, models

from ..services import analog_rollup_diagnostic


class ProductAnalytic(models.Model):
    _inherit = 'product.analytic'

    @api.model
    def _run_analog_rollup_diagnostic(
        self,
        product_codes=None,
        contract_references=None,
        all_contracts=False,
        date_from=None,
        date_to=None,
        watch_quantities=None,
    ):
        """Run diagnostics in an isolated PostgreSQL READ ONLY transaction."""
        diagnostic_cr = self.env.registry.cursor()
        try:
            try:
                # This must be the first SQL statement on the isolated cursor.
                diagnostic_cr.execute('SET TRANSACTION READ ONLY')
                diagnostic_cr.execute('SHOW transaction_read_only')
                read_only_state = diagnostic_cr.fetchone()[0]
            except Exception as error:
                raise RuntimeError(
                    'Could not enable PostgreSQL READ ONLY mode; '
                    'diagnostics aborted.'
                ) from error
            if str(read_only_state).lower() not in {'on', 'true', '1'}:
                raise RuntimeError(
                    'PostgreSQL did not confirm READ ONLY mode; '
                    'diagnostics aborted.'
                )

            diagnostic_env = api.Environment(
                diagnostic_cr,
                self.env.uid,
                dict(self.env.context),
            )
            return analog_rollup_diagnostic.run(
                diagnostic_env,
                product_codes=product_codes,
                contract_references=contract_references,
                all_contracts=all_contracts,
                date_from=date_from,
                date_to=date_to,
                watch_quantities=watch_quantities,
            )
        finally:
            diagnostic_cr.rollback()
            diagnostic_cr.close()
