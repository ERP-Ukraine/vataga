"""Read-only posting blockers, never permission to call a stock API.

A closed, batch-linked timer alone is NOT a costing allocation. The missing
journal/allocator and integration validation remain unconditional blockers.
"""


def posting_blocker_codes(timers):
    codes = [
        'posting_journal_missing', 'interval_allocation_missing',
        'native_genealogy_overwrite', 'enterprise_validation_pending',
    ]
    if any(not timer.get('end') for timer in timers):
        codes.append('open_intervals')
    if any(not timer.get('has_execution_batch') for timer in timers):
        codes.append('unmapped_intervals')
    return codes
