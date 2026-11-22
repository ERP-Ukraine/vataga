"""Prove a C residual from immutable B facts, never from a requested quantity."""
import math


def residual_scope(snapshot, current_sources, released, tracking):
    """Return exact whole-piece source scopes, or reject the entire decision.

    released maps source line IDs to quantities reconstructed from done release
    lines. There is deliberately no sample-number or requested-quantity input.
    """
    def whole(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0 and value == int(value))

    if not isinstance(snapshot, dict) or not snapshot.get('plan', {}).get('ready'):
        raise ValueError('missing_disposition')
    plan = snapshot['plan']
    if plan.get('unidentified') or plan.get('reasons') or tracking not in ('none', 'lot', 'serial'):
        raise ValueError('unidentified')
    sources = snapshot.get('sources', [])
    current = {source['id']: source for source in current_sources}
    if not sources or len(current) != len(current_sources) or set(current) != {s['id'] for s in sources}:
        raise ValueError('changed_source')
    identity = ('id', 'move_id', 'company_id', 'product_id', 'lot_id', 'package_id', 'owner_id', 'quantity')
    lots = {lot['lot_id'] or 0: lot for lot in plan.get('lots', [])}
    allocations = {int(key): value for key, value in plan.get('allocations', {}).items()}
    if set(released) - set(current) or set(allocations) - set(current):
        raise ValueError('changed_release')
    seen = set()
    scopes = []
    total = 0
    for source in sources:
        actual = current[source['id']]
        if any(actual.get(key) != source.get(key) for key in identity):
            raise ValueError('changed_source')
        quantity = source.get('quantity')
        permitted = allocations.get(source['id'], 0)
        moved = released.get(source['id'], 0)
        if not all(whole(value) for value in (quantity, permitted, moved)) or moved != permitted or moved > quantity:
            raise ValueError('changed_release')
        total += quantity
        remaining = quantity - moved
        if not remaining:
            continue
        lot_id = source.get('lot_id') or 0
        if lot_id in seen or not actual.get('exclusive') or source.get('package_id') or source.get('owner_id'):
            raise ValueError('ambiguous_identity')
        if tracking != 'none' and not lot_id:
            raise ValueError('ambiguous_identity')
        if tracking == 'serial' and (quantity != 1 or remaining != 1):
            raise ValueError('ambiguous_identity')
        lot = lots.get(lot_id, {})
        if lot.get('unidentified') or lot.get('quantity') != quantity or lot.get('bad') != remaining:
            raise ValueError('unproven_bad')
        seen.add(lot_id)
        scopes.append(dict(source, quantity=remaining))
    if total != plan.get('produced') or sum(s['quantity'] for s in scopes) != plan.get('bad'):
        raise ValueError('unproven_bad')
    if not scopes:
        raise ValueError('no_residual')
    return sorted(scopes, key=lambda source: source['id'])
