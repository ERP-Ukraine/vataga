"""Transaction-local visibility of RFQs created by one native approval action.

No JSON context flag, persistent domain, global monkeypatch or procurement
rewrite. The scope cannot be supplied by RPC. Odoo still performs native
searches/ACL/rules and prepares every purchase value through its full MRO.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_SCOPE = ContextVar('vataga_new_rfq_scope', default=None)


class NewRFQScope:
    def __init__(self, cursor):
        self.cursor = cursor
        self.key = uuid4().hex
        self.order_ids = set()


def current_scope(cursor):
    scope = _SCOPE.get()
    return scope if scope is not None and scope.cursor is cursor else None


@contextmanager
def new_rfq_scope(cursor):
    scope = NewRFQScope(cursor)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)
