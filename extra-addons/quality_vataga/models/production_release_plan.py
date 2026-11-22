"""Pure, conservative allocation of completed QC facts to posted MRP sources.

sample_number is deliberately not an input: ordinal numbers cannot identify
the same physical item across checks. Only a confirmed serial can do that in B1.
All quantities here are whole pieces, never rounded stock quantities.
"""
import math
from collections import defaultdict


def plan_production_release(sources, checks, required_points, tracking):
    result = {'ready': False, 'produced': 0, 'good': 0, 'bad': 0, 'unidentified': 0,
              'allocations': {}, 'reasons': [], 'lots': []}

    def stop(reason):
        result['reasons'] = [reason]
        return result

    def pieces(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value > 0 and value == int(value))

    if tracking not in ('none', 'lot', 'serial') or not required_points or not sources:
        return stop('missing_scope')
    required = set(required_points)
    by_lot = defaultdict(list)
    source_ids = set()
    for source in sources:
        if not source.get('id') or source['id'] in source_ids or not pieces(source.get('quantity')):
            return stop('invalid_source')
        if tracking != 'none' and not source.get('lot_id'):
            return stop('missing_identity')
        source_ids.add(source['id'])
        by_lot[source.get('lot_id') or 0].append(source)
        result['produced'] += int(source['quantity'])
    # The existing matrix records its immutable lot when samples are initialized.
    # Before then an unfinished QC may legitimately have no lot yet.
    if any(check.get('state') not in ('pass', 'fail') for check in checks):
        return stop('pending_checks')
    by_check = defaultdict(dict)
    for check in checks:
        lot = check.get('lot_id') or 0
        point = check.get('point_id')
        if lot not in by_lot or point not in required or point in by_check[lot]:
            return stop('ambiguous_checks')
        by_check[lot][point] = check
    # Finish the whole mandatory scope before allocating any source line.
    for lot, lines in by_lot.items():
        quantity = sum(int(line['quantity']) for line in lines)
        if set(by_check[lot]) != required:
            return stop('missing_checks')
        if tracking == 'serial' and (quantity != 1 or len(lines) != 1):
            return stop('ambiguous_serial')
        for check in by_check[lot].values():
            samples = check.get('samples', [])
            if check.get('state') not in ('pass', 'fail'):
                return stop('pending_checks')
            if (not check.get('structure_valid') or not check.get('equipment_complete')
                    or check.get('automatic_count') != quantity or len(samples) != quantity):
                return stop('invalid_matrix')
            ids = [sample.get('id') for sample in samples]
            if not all(ids) or len(set(ids)) != len(ids):
                return stop('invalid_matrix')
            if any(not sample.get('complete') or sample.get('result') not in ('pass', 'fail')
                   for sample in samples):
                return stop('pending_samples')
            if check['state'] == 'pass' and any(sample['result'] != 'pass' for sample in samples):
                return stop('inconsistent_pass')
    result['ready'] = True
    for lot, lines in by_lot.items():
        quantity = sum(int(line['quantity']) for line in lines)
        lot_checks = list(by_check[lot].values())
        all_pass = all(check['state'] == 'pass' for check in lot_checks)
        good = bad = unidentified = 0
        reason = False
        if all_pass:
            good = quantity
            for line in lines:
                result['allocations'][line['id']] = int(line['quantity'])
        elif tracking == 'serial':
            # Every check above is for this exact serial and exactly one item.
            bad = 1
        elif len(lot_checks) == 1:
            samples = lot_checks[0]['samples']
            bad = sum(sample['result'] == 'fail' for sample in samples)
            if not bad:  # A global FAIL cannot silently become a full release.
                unidentified = quantity
                reason = 'global_fail_without_failed_item'
            else:
                good = quantity - bad
                if good:
                    if len(lines) != 1:
                        reason = 'ambiguous_source_lines'
                    elif not lines[0].get('exclusive'):
                        reason = 'shared_quarantine_identity'
                    elif not lines[0].get('partial_representable', False):
                        reason = 'unrepresentable_quantity'
                    else:
                        result['allocations'][lines[0]['id']] = good
        else:
            # A whole failing matrix proves every item failed. Otherwise no
            # union/intersection of physical items can be inferred from counts.
            if any(all(sample['result'] == 'fail' for sample in check['samples'])
                   for check in lot_checks):
                bad = quantity
            else:
                unidentified = quantity
                reason = 'unit_identity_required'
        result['good'] += good
        result['bad'] += bad
        result['unidentified'] += unidentified
        if reason and reason not in result['reasons']:
            result['reasons'].append(reason)
        result['lots'].append({'lot_id': lot, 'quantity': quantity, 'good': good,
                               'bad': bad, 'unidentified': unidentified, 'reason': reason})
    assert result['good'] + result['bad'] + result['unidentified'] == result['produced']
    return result
