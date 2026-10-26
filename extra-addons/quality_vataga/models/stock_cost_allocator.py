"""Versioned, conservative allocation of CLOSED labour intervals."""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP


POLICY = 'equal_closed_members_v1'


def allocate_intervals(unit_keys, intervals, rounding):
    keys = sorted(unit_keys)
    if not keys or len(set(keys)) != len(keys) or any(not isinstance(key, str) or not key for key in keys):
        raise ValueError('A fixed, distinct named population is required.')
    quantum = Decimal(str(rounding))
    if not quantum.is_finite() or quantum <= 0:
        raise ValueError('Invalid currency rounding.')
    result, previous, seen = [], {}, set()
    for row in sorted(intervals, key=lambda item: (item['workorder'], item['start'], item['id'])):
        members = sorted(row['members'])
        if (row['id'] in seen or not members or len(set(members)) != len(members)
                or not set(members) <= set(keys) or not row.get('end')):
            raise ValueError('Closed intervals with an explicit, distinct named scope are required.')
        seen.add(row['id'])
        start, end = datetime.fromisoformat(row['start']), datetime.fromisoformat(row['end'])
        rate = Decimal(str(row['rate']))
        if end <= start or not rate.is_finite() or rate < 0:
            raise ValueError('Invalid interval duration or hourly rate.')
        if start < previous.get(row['workorder'], start):
            raise ValueError('Overlapping intervals of one operation require an explicit alternative policy.')
        previous[row['workorder']] = end
        seconds = Decimal(str((end - start).total_seconds()))
        total = (seconds * rate / 3600 / quantum).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * quantum
        # Allocate integer currency quanta. Stable order, no cumulative float
        # rounding and no last-unit cost correction after posting.
        quotient, remainder = divmod(int(total / quantum), len(members))
        for index, key in enumerate(members):
            result.append(dict(timer=row['id'], unit_key=key,
                               amount=float((quotient + (index < remainder)) * quantum)))
    return result
