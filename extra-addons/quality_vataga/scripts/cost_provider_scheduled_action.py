# Read-only metadata/source diagnostic; no fixture, posting or authorization.
# Run as system administrator on this exact staging database after restart.
report = env['mrp.production']._native_partial_cost_provider_report(
    'odoo17-vataga-staging', include_source=True)
# Separate log records prevent the whole report being truncated as one entry.
for method in ('mrp.production._cal_price', 'mrp.workorder._cal_cost'):
    log('COST PROVIDER MRO: ' + method, level='info')
    for provider in report[method]:
        log(str(provider), level='info')
log('COST FIELDS: ' + str(report['fields']), level='info')
for method in sorted(report['computed_methods']):
    log('COST COMPUTE: ' + method + ' ' + str(report['computed_methods'][method]), level='info')
