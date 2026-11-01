"""Read-only audit comparisons and call tracing; no cost or posting policy."""
import copy
import sys
from contextlib import contextmanager


def value_differences(before, after, path=''):
    """Exact differences, including missing keys; no rounding or field exclusions."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after), key=str):
            child = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
            if key not in before or key not in after:
                result.append({'path': child, 'before_present': key in before, 'after_present': key in after,
                               'before': copy.deepcopy(before.get(key)), 'after': copy.deepcopy(after.get(key))})
            else:
                result.extend(value_differences(before[key], after[key], child))
        return result
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        if len(before) == len(after):
            return [difference for index, (old, new) in enumerate(zip(before, after))
                    for difference in value_differences(old, new, path + '/' + str(index))]
    return [] if before == after else [{'path': path or '/', 'before': copy.deepcopy(before),
                                       'after': copy.deepcopy(after)}]


def analytic_state_difference(before, after):
    old_lines = {str(row['id']): row for row in before.get('analytic_lines', [])}
    new_lines = {str(row['id']): row for row in after.get('analytic_lines', [])}
    line_changes = {line_id: value_differences(old_lines[line_id], new_lines[line_id])
                    for line_id in sorted(set(old_lines) & set(new_lines), key=int)
                    if old_lines[line_id] != new_lines[line_id]}
    changed = sorted(key for key in set(before) | set(after)
                     if key not in before or key not in after or before[key] != after[key])
    return {'equal': before == after, 'changed_sections': changed,
            'analytic_lines': {
                'before_ids': [row['id'] for row in before.get('analytic_lines', [])],
                'after_ids': [row['id'] for row in after.get('analytic_lines', [])],
                'added': [copy.deepcopy(new_lines[key]) for key in sorted(set(new_lines) - set(old_lines), key=int)],
                'removed': [copy.deepcopy(old_lines[key]) for key in sorted(set(old_lines) - set(new_lines), key=int)],
                'changed_by_id': line_changes},
            'sections': {key: value_differences({key: before[key]} if key in before else {},
                                               {key: after[key]} if key in after else {})
                         for key in changed if key != 'analytic_lines'}}


@contextmanager
def trace_duration_calls(workorder):
    """Observe real methods without patching MRO, reading fields or recomputing.

    Cache reads deliberately bypass ORM field access. An existing profiler is
    preserved; inability to trace is reported explicitly, never hidden.
    """
    trace = {'status': 'RUNNING', 'events': []}
    previous = sys.getprofile()
    if previous is not None:
        trace.update(status='NOT_RUN', reason='An existing thread profiler is active')
        yield trace
        return
    codes = {}
    for owner in type(workorder).__mro__:
        for name in ('_compute_duration', 'get_duration', '_create_or_update_analytic_entry',
                     '_prepare_analytic_line_values', '_set_duration'):
            method = owner.__dict__.get(name)
            seen = set()
            while method is not None and id(method) not in seen:
                seen.add(id(method))
                code = getattr(method, '__code__', None)
                if code is not None:
                    codes.setdefault(code, []).append(owner.__module__ + '.' + owner.__qualname__ + '.' + name)
                method = getattr(method, '__wrapped__', None)
    fixture_ids = set(workorder.ids)

    def observe(frame, event, argument):
        if event not in ('call', 'return') or frame.f_code not in codes:
            return
        try:
            records = frame.f_locals.get('self')
            if (getattr(records, '_name', None) != 'mrp.workorder'
                    or records.env.cr is not workorder.env.cr or not set(records.ids).issubset(fixture_ids)):
                return
            if len(trace['events']) >= 1000:
                trace.update(status='TRUNCATED', reason='More than 1000 native calls')
                return
            duration = records._fields['duration']
            trace['events'].append({
                'event': event, 'providers': codes[frame.f_code], 'ids': records.ids,
                'cached_duration': {str(record.id): records.env.cache.get(record, duration, '<not_cached>')
                                    for record in records},
                'file': frame.f_code.co_filename, 'line': frame.f_lineno})
        except Exception as error:
            trace.update(status='ERROR', error_type=type(error).__name__, error=str(error))

    try:
        sys.setprofile(observe)
        yield trace
    finally:
        sys.setprofile(previous)
        if trace['status'] == 'RUNNING':
            trace['status'] = 'COMPLETE'
