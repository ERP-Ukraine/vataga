# STAGING only, administrator, after module upgrade (version remains .62).
# Recheck all 14 employee cases before granting the temporary fixture scope.
# No production approval; manual_periodic accounting remains pending.
audit = env['mrp.production']._run_native_employee_cost_audit('odoo17-vataga-staging')
log('PRE-INTEGRATION AUDIT: ' + str({key: audit[key] for key in audit if key not in ('sources', 'checks', 'baseline')}), level='info')
for check in audit.get('checks', []):
    log('PRE-INTEGRATION CASE: ' + str(check), level='info')
if audit.get('result') == 'AUDIT_PASS' and audit.get('passed') == 14 and audit.get('failed') == 0 and audit.get('not_run') == 0 and audit.get('rollback') == 'COMPLETE':
    outcomes = []
    for tracking in ('none', 'lot'):
        result = env['mrp.production']._run_quality_production_loss_probe('odoo17-vataga-staging', tracking=tracking)
        outcomes.append(result)
        log('PRODUCTION LOSS SUMMARY: ' + str({key: result[key] for key in result if key not in ('posted_evidence', 'checks', 'boundaries', 'cost_mro')}), level='info')
        for key in ('cost_mro', 'boundaries', 'checks', 'posted_evidence'):
            log('PRODUCTION LOSS ' + tracking + ' ' + key + ': ' + str(result.get(key)), level='info')
    log('INTEGRATION: ' + str({'result': 'PASS' if all(r.get('result') == 'PASS' and r.get('rollback') == 'COMPLETE' and r.get('passed') == 24 and r.get('not_run') == 0 for r in outcomes) else 'FAIL', 'production_provider_authorized': False, 'accounting_status': 'manual_periodic_pending'}), level='info')
else:
    log('INTEGRATION: NOT_RUN; employee audit did not pass all 14 checks with COMPLETE rollback', level='warning')
