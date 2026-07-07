import logging

import psycopg2
from psycopg2 import errorcodes

from odoo import models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def _set_last_seen_message(self, last_message, *args, **kwargs):
        """Advance the "last seen / fetched" markers without triggering a
        serialization retry storm.

        In Odoo 17 this method lives on ``discuss.channel`` and does
        ``member.write({'seen_message_id': ..., 'fetched_message_id': ...,
        'last_seen_dt': ...})``. Many concurrent chat clients hit the same
        discuss_channel_member row, so under PostgreSQL REPEATABLE READ this
        raises "could not serialize access due to concurrent update". Odoo's
        service.model.retrying() then retries the WHOLE rpc up to 5 times with
        time.sleep() backoff, blocking the single-threaded prefork HTTP worker
        for seconds and starving business requests.

        These markers are best-effort (the next fetched/seen event advances
        them again), so run the write inside a SAVEPOINT and swallow the
        serialization failure instead of letting it force a retry.
        """
        cr = self.env.cr
        # Flush unrelated pending work OUTSIDE the savepoint so a genuine
        # serialization failure on other records still propagates and retries
        # normally (we must only swallow the presence write below).
        self.env.flush_all()
        try:
            with cr.savepoint():
                res = super()._set_last_seen_message(last_message, *args, **kwargs)
                # Force the presence UPDATE (and its bus notification) to run
                # now, inside the savepoint, so a conflict is caught here.
                self.env.flush_all()
        except psycopg2.OperationalError as e:
            if e.pgcode != errorcodes.SERIALIZATION_FAILURE:
                raise
            _logger.debug(
                "discuss_perf_fix: skipped concurrent last-seen update for "
                "channel(s) %s",
                self.ids,
            )
            return None
        return res

    def channel_fetched(self):
        """Broadcast the "fetched" marker without triggering a retry storm.

        Core already runs the UPDATE with ``FOR NO KEY UPDATE SKIP LOCKED`` to
        skip rows locked by *uncommitted* transactions, but under REPEATABLE
        READ that still raises "could not serialize access due to concurrent
        update" when another transaction has *committed* a change to the row
        since our snapshot. That serialization failure reaches
        service.model.retrying(), which retries the whole rpc with time.sleep()
        backoff and blocks the prefork HTTP worker.

        The fetched marker is best-effort (the next fetch advances it), so wrap
        the call in a SAVEPOINT and swallow the serialization failure.
        """
        cr = self.env.cr
        # Flush unrelated pending work outside the savepoint so genuine
        # conflicts elsewhere still propagate and retry normally.
        self.env.flush_all()
        try:
            with cr.savepoint():
                res = super().channel_fetched()
                # Force any pending bus notification to flush inside the
                # savepoint so a conflict is caught here.
                self.env.flush_all()
        except psycopg2.OperationalError as e:
            if e.pgcode != errorcodes.SERIALIZATION_FAILURE:
                raise
            _logger.debug(
                "discuss_perf_fix: skipped concurrent channel_fetched for "
                "channel(s) %s",
                self.ids,
            )
            return None
        return res
