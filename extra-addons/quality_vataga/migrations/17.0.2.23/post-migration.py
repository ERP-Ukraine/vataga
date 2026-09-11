from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    # Board stages are noupdate: rename only our approval stage on upgrade.
    env = api.Environment(cr, SUPERUSER_ID, {})
    stage = env.ref('quality_vataga.quality_alert_stage_board_approval')
    stage.with_context(lang=None).write({'name': 'На затвердженні'})
    if 'uk_UA' in dict(env['res.lang'].get_installed()):
        stage.with_context(lang='uk_UA').write({'name': 'На затвердженні'})
