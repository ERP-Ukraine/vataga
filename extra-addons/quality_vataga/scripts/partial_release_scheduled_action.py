# One temporary Scheduled Action on STAGING, after restarting and upgrading
# quality_vataga to create the new audit/checkpoint tables (version stays .62).
# Set the exact staging database explicitly. No production MO is selected.
# The server helper always rolls its fixture back; only this single log persists.
# Synthetic closed/open timer preparation is reported, not Shop Floor proof.
expected_database = 'REPLACE_WITH_EXACT_STAGING_DATABASE'
result = env['mrp.production']._run_quality_partial_release_probe(expected_database, tracking='none')
log('QUNIT PARTIAL RELEASE: ' + str(result), level='info')
