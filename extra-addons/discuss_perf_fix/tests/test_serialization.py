from unittest.mock import patch

import psycopg2
from psycopg2 import errorcodes

import odoo
from odoo.tests import TransactionCase, tagged


class _FakeSerializationFailure(psycopg2.OperationalError):
    """psycopg2.OperationalError whose pgcode reports a serialization failure.

    psycopg2 exposes ``pgcode`` as a read-only attribute on real error
    instances, so we shadow it with a class attribute to build a controllable
    fake that still passes ``isinstance(e, psycopg2.OperationalError)`` and the
    ``e.pgcode == '40001'`` check in the overrides.
    """

    pgcode = errorcodes.SERIALIZATION_FAILURE


class _FakeDeadlock(psycopg2.OperationalError):
    pgcode = errorcodes.DEADLOCK_DETECTED


def _faulty_execute(match, error_cls):
    """Build a Cursor.execute replacement that raises `error_cls` for queries
    matched by `match(query_str)`, and delegates everything else to the real
    execute.
    """
    real_execute = odoo.sql_db.Cursor.execute

    def execute(self, query, params=None, log_exceptions=None):
        try:
            query_str = str(query)
        except Exception:  # pragma: no cover - defensive
            query_str = ""
        if match(query_str):
            raise error_cls("simulated failure on discuss_channel_member update")
        return real_execute(self, query, params, log_exceptions=log_exceptions)

    return execute


def _is_member_update(query_str):
    low = query_str.lower()
    return "discuss_channel_member" in low and "update" in low


@tagged("post_install", "-at_install")
class TestDiscussSerializationSwallow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.peer = cls.env["res.partner"].create({"name": "Peer"})
        # Odoo 17: discuss.channel.channel_get(partners_to) creates/returns the
        # 1:1 chat channel (there is no _get_or_create_chat in 17.0).
        cls.chat = cls.env["discuss.channel"].channel_get(cls.peer.ids)
        cls.message = cls.chat.message_post(
            body="hello", message_type="comment", subtype_xmlid="mail.mt_comment"
        )
        cls.member = cls.chat.channel_member_ids.filtered(
            lambda m: m.partner_id == cls.env.user.partner_id
        )

    def _reset_markers(self):
        # Clear the presence markers (without the patch active) so the overridden
        # methods actually attempt an UPDATE we can intercept.
        self.member.write(
            {"seen_message_id": False, "fetched_message_id": False}
        )
        self.env.flush_all()

    def test_set_last_seen_swallows_serialization_failure(self):
        """A serialization failure on the presence write is swallowed and the
        rpc does not raise (so service.model.retrying() never kicks in)."""
        self._reset_markers()
        faulty = _faulty_execute(_is_member_update, _FakeSerializationFailure)
        with patch.object(odoo.sql_db.Cursor, "execute", faulty):
            # Must NOT raise.
            self.chat._set_last_seen_message(self.message)
        # Marker was rolled back (write lost the race) — that is acceptable.
        self.assertFalse(
            self.member.seen_message_id,
            "seen_message_id should stay unset after a swallowed conflict",
        )

    def test_channel_fetched_swallows_serialization_failure(self):
        """channel_fetched (raw SKIP LOCKED UPDATE) also swallows the conflict."""
        self._reset_markers()
        faulty = _faulty_execute(_is_member_update, _FakeSerializationFailure)
        with patch.object(odoo.sql_db.Cursor, "execute", faulty):
            # Must NOT raise.
            self.chat.channel_fetched()
        self.assertFalse(
            self.member.fetched_message_id,
            "fetched_message_id should stay unset after a swallowed conflict",
        )

    def test_non_serialization_error_propagates(self):
        """A different OperationalError (e.g. deadlock) must NOT be swallowed."""
        self._reset_markers()
        faulty = _faulty_execute(_is_member_update, _FakeDeadlock)
        with patch.object(odoo.sql_db.Cursor, "execute", faulty):
            with self.assertRaises(psycopg2.OperationalError) as cm:
                self.chat._set_last_seen_message(self.message)
        self.assertEqual(cm.exception.pgcode, errorcodes.DEADLOCK_DETECTED)

    def test_normal_path_still_updates_markers(self):
        """Without any conflict the markers are advanced as usual."""
        self._reset_markers()
        self.chat._set_last_seen_message(self.message)
        self.assertEqual(self.member.seen_message_id, self.message)
        self.assertEqual(self.member.fetched_message_id, self.message)
