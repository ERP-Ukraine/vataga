import logging

from lxml import etree

from odoo import SUPERUSER_ID, api


_logger = logging.getLogger(__name__)

VIEW_XMLID = 'account_vataga.view_move_form_account'
FIELD_NAME = 'has_checked_moderation_fields'


def _remove_stale_field_from_arch(arch):
    root = etree.fromstring(arch.encode('utf-8'))
    removed_fields = 0
    removed_xpaths = 0

    for node in root.xpath(".//field[@name='%s']" % FIELD_NAME):
        parent = node.getparent()
        if parent is None:
            continue

        parent.remove(node)
        removed_fields += 1

        if parent.tag == 'xpath' and not len(parent) and not (parent.text or '').strip():
            grandparent = parent.getparent()
            if grandparent is not None:
                grandparent.remove(parent)
                removed_xpaths += 1

    if not removed_fields:
        return arch, removed_fields, removed_xpaths

    return etree.tostring(root, encoding='unicode'), removed_fields, removed_xpaths


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    view = env.ref(VIEW_XMLID, raise_if_not_found=False)

    if not view:
        _logger.info("View %s was not found; no stale %s cleanup needed", VIEW_XMLID, FIELD_NAME)
        return

    arch = view.arch_db or ''
    if FIELD_NAME not in arch:
        _logger.info("View %s does not contain stale %s reference", VIEW_XMLID, FIELD_NAME)
        return

    try:
        clean_arch, removed_fields, removed_xpaths = _remove_stale_field_from_arch(arch)
    except etree.XMLSyntaxError:
        _logger.warning("Cannot parse view %s while removing stale %s reference", VIEW_XMLID, FIELD_NAME)
        return

    if removed_fields:
        view.write({'arch_db': clean_arch})

    _logger.info(
        "Cleaned view %s: removed %s stale %s field node(s) and %s empty xpath node(s)",
        VIEW_XMLID,
        removed_fields,
        FIELD_NAME,
        removed_xpaths,
    )
