"""Retired release APIs have no capability tokens or configuration switches."""
from odoo import _, api
from odoo.exceptions import UserError

ARCHIVE_MESSAGE = 'Архів 13.4А/13.4Б доступний лише для читання. Створення дозволів, планів, розподілів та власне складське проведення вимкнено.'


def archive_disabled():
    raise UserError(_(ARCHIVE_MESSAGE))


class RetiredDialog:
    """Preserve unknown transient rows and schemas until database inventory."""

    @api.model_create_multi
    def create(self, vals_list):
        archive_disabled()

    def write(self, vals):
        archive_disabled()

    def unlink(self):
        archive_disabled()

    def action_apply(self):
        archive_disabled()

    def _transient_vacuum(self):
        return
