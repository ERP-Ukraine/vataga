"""Pure tests of server-stack scoping; not substitutes for Enterprise ORM."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('stock_bridge_authority_probe', Path(__file__).resolve().parents[1] / 'models/stock_bridge_authority.py')
authority = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authority)


class ScopeIdentity:
    def __init__(self, production_id=1, user_id=2):
        self.id, self.env = production_id, SimpleNamespace(uid=user_id)

    def __len__(self):
        return 1


class TestStockBridgeAuthority(unittest.TestCase):
    def test_default_buttons_are_always_denied(self):
        production = ScopeIdentity()
        with authority.posting_authority(1, 2, closing=True):
            self.assertFalse(authority.permits(production, None))
            self.assertFalse(authority.permits(production, 'button_mark_done'))

    def test_only_current_mo_actor_and_stock_purposes_are_allowed(self):
        with authority.posting_authority(1, 2):
            self.assertTrue(authority.permits(ScopeIdentity(), 'stock_move'))
            self.assertFalse(authority.permits(ScopeIdentity(3), 'stock_move'))
            self.assertFalse(authority.permits(ScopeIdentity(user_id=3), 'stock_line'))
            self.assertFalse(authority.permits(ScopeIdentity(), 'final_workorder'))

    def test_final_close_requires_its_own_scope_and_restores_parent(self):
        production = ScopeIdentity()
        with authority.posting_authority(1, 2):
            with authority.posting_authority(1, 2, closing=True):
                self.assertTrue(authority.permits(production, 'final_workorder'))
            self.assertFalse(authority.permits(production, 'final_workorder'))
        self.assertFalse(authority.permits(production, 'stock_move'))

    def test_exception_cannot_leave_authority_enabled(self):
        with self.assertRaises(ValueError):
            with authority.posting_authority(1, 2):
                raise ValueError('rollback')
        self.assertFalse(authority.permits(ScopeIdentity(), 'stock_move'))


if __name__ == '__main__':
    unittest.main()
