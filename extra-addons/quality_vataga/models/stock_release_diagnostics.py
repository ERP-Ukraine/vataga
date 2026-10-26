"""Enterprise bridge blockers, never permission to call a stock API.

A closed, batch-linked timer alone is NOT a costing allocation. The missing
integration bridge and validation remain unconditional blockers.
"""


def posting_blocker_codes(timers):
    codes = [
        'enterprise_bridge_pending', 'interval_scope_required',
        'native_post_inventory_forbidden', 'enterprise_validation_pending',
    ]
    if any(not timer.get('end') for timer in timers):
        codes.append('open_intervals')
    if any(not timer.get('has_execution_batch') for timer in timers):
        codes.append('unmapped_intervals')
    return codes
