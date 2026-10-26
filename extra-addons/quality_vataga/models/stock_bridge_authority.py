"""Server-stack authority for ONE validated posting, never RPC context.

Default/native buttons are never authorized. No global switch or test-mode
escape exists. The public Enterprise validation gate remains unconditional.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar('vataga_validated_posting', default=None)


@contextmanager
def posting_authority(production_id, user_id, closing=False):
    token = _active.set((production_id, user_id, closing))
    try:
        yield
    finally:
        _active.reset(token)


def permits(productions, purpose):
    if purpose not in ('stock_move', 'stock_line', 'final_workorder', 'final_production'):
        return False
    authority = _active.get()
    if len(productions) != 1 or not authority or authority[:2] != (productions.id, productions.env.uid):
        return False
    return authority[2] if purpose.startswith('final_') else True
