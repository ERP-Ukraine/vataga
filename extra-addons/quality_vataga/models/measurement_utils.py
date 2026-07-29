import math

from odoo import _
from odoo.exceptions import ValidationError


BOOLEAN_TRUE_VALUES = {'1', 'true', 'yes', 'так'}
BOOLEAN_FALSE_VALUES = {'0', 'false', 'no', 'ні'}


def normalize_boolean_norm(raw_value):
    value = (raw_value or '').strip().casefold()
    if value in BOOLEAN_TRUE_VALUES:
        return 'yes'
    if value in BOOLEAN_FALSE_VALUES:
        return 'no'
    return False


def parse_numeric_input(raw_value, field_label):
    value = str(raw_value or '').strip()
    if not value:
        return False, 0.0

    normalized_value = (
        value
        .replace('\N{NO-BREAK SPACE}', '')
        .replace(' ', '')
        .replace(',', '.')
    )
    try:
        parsed_value = float(normalized_value)
    except (TypeError, ValueError) as error:
        raise ValidationError(_(
            '%(field)s повинен бути числом.',
            field=field_label,
        )) from error
    if not math.isfinite(parsed_value):
        raise ValidationError(_(
            '%(field)s повинен бути скінченним числом.',
            field=field_label,
        ))
    return True, parsed_value


def format_number(value):
    return format(value, '.15g')
