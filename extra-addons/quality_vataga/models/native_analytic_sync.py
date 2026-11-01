"""Synchronize managed WO analytics after duration; never approve cost providers."""
import hashlib
import inspect
from contextlib import contextmanager
from contextvars import ContextVar

from odoo import models
from odoo.addons.mrp.models.mrp_workorder import MrpWorkorder as CoreWorkorder
from odoo.addons.mrp_account.models.mrp_workorder import MrpWorkorder as AccountWorkorder

from .native_cost_provider import method_definitions


_SYNC_SCOPE = ContextVar('quality_native_analytic_sync_scope', default=frozenset())
_DEFERRED = ContextVar('quality_native_analytic_deferred', default=frozenset())
_REVIEWED_DURATION = (
    ('odoo.addons.mrp_workorder_hr_account.models.mrp_workorder', 'MrpWorkorder._compute_duration',
     'bc14ea261933e6b4a0f18630bbd89c9c84a908d7119d1b3b89fa8f696d85ca71'),
    ('odoo.addons.mrp_workorder.models.mrp_workorder', 'MrpProductionWorkcenterLine._compute_duration',
     '8ab93fe310949670b9c0e2cdbb606f1d69d768844e3cfb4f645fe85050c71426'),
)


def _keys(workorders):
    return frozenset((workorders.env.cr, workorder.id) for workorder in workorders)


@contextmanager
def analytic_sync_scope(workorders):
    """Private Python scope for the rollback audit's own fresh WO records."""
    token = _SYNC_SCOPE.set(_SYNC_SCOPE.get() | _keys(workorders))
    try:
        yield
    finally:
        _SYNC_SCOPE.reset(token)


class NativeDurationAnalyticSync(models.Model):
    _inherit = 'mrp.workorder'

    def _native_analytic_duration_chain_reviewed(self):
        """Source attestation for synchronization only, not _cal_cost admission."""
        definitions = method_definitions(self, '_compute_duration')
        methods = [method for owner, method in definitions]
        helpers = method_definitions(self, '_create_or_update_analytic_entry')
        if (not methods or methods[0] is not NativeDurationAnalyticSync._compute_duration
                or not helpers or helpers[0][1] is not NativeDurationAnalyticSync._create_or_update_analytic_entry):
            # A later addon must not run an unguarded pre-helper ahead of us,
            # or pass an empty recordset before our duration wrapper is reached.
            return False
        reviewed = []
        for owner, method in definitions:
            if method in (NativeDurationAnalyticSync._compute_duration,
                          CoreWorkorder._compute_duration, AccountWorkorder._compute_duration):
                continue
            try:
                item = (owner.__module__, method.__qualname__,
                        hashlib.sha256(inspect.getsource(method).encode('utf-8')).hexdigest())
            except (AttributeError, TypeError, OSError):
                return False
            if (getattr(method, '__wrapped__', None) is not None or getattr(method, '__module__', None) != owner.__module__
                    or item not in _REVIEWED_DURATION):
                return False
            reviewed.append(item)
        return tuple(reviewed) == _REVIEWED_DURATION

    def _native_analytic_sync_workorders(self):
        scoped = _SYNC_SCOPE.get()
        managed = self.filtered(lambda wo: (wo.env.cr, wo.id) in scoped)
        for workorder in self - managed:
            production = workorder.production_id
            if (('quality_unit_route_required' in production._fields and production.quality_unit_route_required
                    and not production.quality_transfer_controlled)
                    or production.quality_native_partial_revision):
                managed |= workorder
        return managed

    def _compute_duration(self):
        managed = self._native_analytic_sync_workorders()
        if not managed or not self._native_analytic_duration_chain_reviewed():
            return super()._compute_duration()
        # The reviewed Enterprise head calls analytics before assigning the
        # new duration; its tail passes an empty recordset to mrp_account.
        # Defer managed callbacks (including inverse callbacks) until after
        # the real duration calculation, then invoke the native helper ONCE.
        token = _DEFERRED.set(_DEFERRED.get() | _keys(managed))
        try:
            result = super()._compute_duration()
        finally:
            _DEFERRED.reset(token)
        managed._create_or_update_analytic_entry()
        return result

    def _create_or_update_analytic_entry(self):
        deferred = _DEFERRED.get()
        if not deferred:
            return super()._create_or_update_analytic_entry()
        ready = self.filtered(lambda wo: (wo.env.cr, wo.id) not in deferred)
        return super(NativeDurationAnalyticSync, ready)._create_or_update_analytic_entry()
