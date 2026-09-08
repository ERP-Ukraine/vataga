import logging

from odoo import Command, api, models


_logger = logging.getLogger(__name__)


class ResGroups(models.Model):
    _inherit = 'res.groups'

    @api.model
    def _link_payment_unreconcile_group(self):
        """Update the UI-created group on installation and every module upgrade.

        A record tag with a __custom__ XML ID requires an installed __custom__
        module in Odoo's XML loader. Resolve the existing record instead; never
        create a replacement on databases without this deployment-specific role.
        """
        moderator = self.env.ref(
            '__custom__.user_group_for_moderation', raise_if_not_found=False
        )
        if not moderator:
            _logger.warning(
                'Payment unreconcile inheritance skipped: '
                '__custom__.user_group_for_moderation does not exist. '
                'Upgrade account_vataga again after restoring the External ID.'
            )
            return
        if moderator._name != 'res.groups':
            raise ValueError(
                '__custom__.user_group_for_moderation must reference res.groups'
            )
        group = self.env.ref('account_vataga.group_account_payment_unreconcile')
        moderator.write({'implied_ids': [Command.link(group.id)]})
