from odoo import SUPERUSER_ID, api
from odoo.tools import sql


def migrate(cr, version):
    if not sql.column_exists(cr, 'mail_message', 'product_autolog_company_id'):
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute(
        """
        UPDATE mail_message
           SET record_company_id = product_autolog_company_id,
               subtype_id = %s
         WHERE subtype_id = %s
           AND product_autolog_company_id IS NOT NULL
        """,
        (
            env.ref('product_vataga.mt_product_autolog_company').id,
            env.ref('product_vataga.mt_product_autolog').id,
        ),
    )
    env['mail.message'].invalidate_model(['record_company_id', 'subtype_id'])
