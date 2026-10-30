# STAGING only, administrator, after module upgrade (version remains .62).
# Real Quality/Stock/MRP/employee costing, new fixture always rolled back.
for tracking in ('none', 'lot'):
    result = env['mrp.production']._run_quality_production_loss_probe('odoo17-vataga-staging', tracking=tracking)
    log('PRODUCTION LOSS: ' + str(result), level='info')
