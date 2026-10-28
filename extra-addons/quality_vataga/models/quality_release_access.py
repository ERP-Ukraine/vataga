"""Existing AND-role restriction retained for reading historical stock data."""

from odoo import _
from odoo.exceptions import AccessError


STOCK_RELEASE_GROUPS = ('mrp.group_mrp_user', 'stock.group_stock_user')


def has_stock_release_roles(user):
    # ACL groups and field/view groups are additive (OR), never an AND.
    return all(user.has_group(group) for group in STOCK_RELEASE_GROUPS)


def check_stock_release_roles(user, raise_exception=True):
    if has_stock_release_roles(user):
        return True
    if raise_exception:
        raise AccessError(_('Для читання складського архіву потрібні обидві ролі: «Виробництво / Користувач» та «Склад / Користувач».'))
    return False
