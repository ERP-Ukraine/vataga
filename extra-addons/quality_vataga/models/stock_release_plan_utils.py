"""Exact quantity calculations for a proposal, never stock posting."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class StockReleasePlanError(ValueError):
    pass


def _decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise StockReleasePlanError('Quantity must be a finite number.') from None
    if not result.is_finite():
        raise StockReleasePlanError('Quantity must be a finite number.')
    return result


def exact_quantity(quantity, rounding):
    """Accept representable nonnegative quantities, allowing only float noise."""
    quantity, rounding = _decimal(quantity), _decimal(rounding)
    if quantity < 0 or rounding <= 0:
        raise StockReleasePlanError('Quantity cannot be negative; rounding must be positive.')
    rounded = (quantity / rounding).to_integral_value(rounding=ROUND_HALF_UP) * rounding
    if abs(quantity - rounded) > rounding * Decimal('0.000000001'):
        raise StockReleasePlanError('Partial quantity is not representable in this unit of measure.')
    return float(rounded)


def proportional_quantity(total, selected, batch, rounding):
    if (type(selected) is not int or type(batch) is not int
            or not 0 < selected <= batch):
        raise StockReleasePlanError('Select a positive whole number of units within the fixed batch.')
    total = _decimal(total)
    if total <= 0:
        raise StockReleasePlanError('Component demand must be positive.')
    if not exact_quantity(total, rounding):
        raise StockReleasePlanError('Component demand cannot disappear through rounding.')
    quantity = exact_quantity(total * selected / batch, rounding)
    if not quantity:
        raise StockReleasePlanError('Partial component demand cannot disappear through rounding.')
    return quantity


def allocate_reserved_quantity(quantity, reservations, rounding):
    """Return (line id, quantity) slices without changing reservations."""
    remaining = _decimal(exact_quantity(quantity, rounding))
    result, seen = [], set()
    for line_id, available in reservations:
        if not line_id or line_id in seen:
            raise StockReleasePlanError('Reservation lines must have distinct identifiers.')
        seen.add(line_id)
        available = _decimal(exact_quantity(available, rounding))
        taken = min(remaining, available)
        if taken:
            result.append((line_id, float(taken)))
            remaining -= taken
    if remaining:
        raise StockReleasePlanError('Reserved component quantity is insufficient.')
    return result
