from . import models


def post_init_hook(env):
    env['stock.picking.type']._configure_cab_quality_quarantine()
