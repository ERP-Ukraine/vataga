report = env['mrp.production']._run_quality_actual_batch_acceptance_probe(
    expected_database='odoo17-vataga-staging'
)
raise Exception(str(report))
