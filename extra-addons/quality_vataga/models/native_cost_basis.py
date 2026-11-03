"""Cost-input comparison only. This module never authorizes a provider or Stock."""

BASIS_VERSION = 2


def checkpoint_basis_error(current, previous):
    if previous.get('version') != BASIS_VERSION or current.get('version') != BASIS_VERSION:
        return 'Старий знімок розрахунку вартості не містить повного базису вартості. Автоматичне перенесення заборонене.'
    required = {'workcenter', 'company', 'rate', 'dependencies', 'timers', 'timer_values', 'employee_contract'}
    if previous.get('employee_contract') == 'enterprise_employee_v1':
        required |= {'employee_rates', 'employee_companies', 'employee_timers', 'employee_fallback_rate'}
    if (required - set(previous) or required - set(current)
            or set(previous.get('timer_values', {})) != set(previous.get('timers', {}))
            or (previous.get('employee_contract') == 'enterprise_employee_v1'
                and set(previous.get('employee_timers', {})) != set(previous.get('timers', {})))):
        return 'Знімок розрахунку вартості містить неповні вхідні дані вартості.'
    for name in ('workcenter', 'company', 'rate', 'dependencies', 'employee_contract', 'employee_fallback_rate'):
        if current.get(name) != previous.get(name):
            return 'Змінено початкові дані, ставку або спосіб розрахунку вже врахованої вартості робочого завдання.'
    # New employees/intervals may be added, but every previously booked input
    # must remain identical even when compensating changes preserve the total.
    for name in ('timers', 'timer_values', 'employee_rates', 'employee_companies', 'employee_timers'):
        if any(current.get(name, {}).get(key) != value for key, value in previous.get(name, {}).items()):
            return 'Змінено вже враховані робочі інтервали, працівника, тривалість або вартість робочого завдання.'
    if not previous.get('timers'):
        return 'Знімок розрахунку вартості не містить підтверджених інтервалів.'
    cutoff = max(value[1] for value in previous['timers'].values())
    if any(value[0] < cutoff for key, value in current['timers'].items() if key not in previous['timers']):
        return 'Новий інтервал перекриває вже капіталізовану роботу.'
    return None
