# STAGING administrator; new cost/analytic fixtures always rolled back.
# No production provider admission, no Stock posting, no checkpoint creation.
report = env['mrp.production']._run_native_employee_cost_audit('odoo17-vataga-staging')
log('EMPLOYEE COST AUDIT: ' + str({key: report[key] for key in report if key not in ('sources', 'checks', 'baseline')}), level='info')
for candidate in report['sources']['enterprise_cost_candidate']:
    log('EMPLOYEE COST CANDIDATE: ' + str(candidate), level='info')
for method in sorted(report['sources']['analytic_methods']):
    for provider in report['sources']['analytic_methods'][method]:
        log('ANALYTIC SOURCE: ' + method + ' ' + str(provider), level='info')
if 'baseline' in report:
    log('EMPLOYEE COST BASELINE: ' + str(report['baseline']), level='info')
for check in report.get('checks', []):
    log('EMPLOYEE COST CASE: ' + str(check), level='info')
