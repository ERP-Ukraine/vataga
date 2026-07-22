#!/usr/bin/env python3
"""Thin Odoo-shell wrapper around the module diagnostic service."""

from odoo.addons.product_alternatives_vataga.services import (
    analog_rollup_diagnostic,
)


if "env" not in globals():
    raise RuntimeError(
        "This script must be executed through Odoo shell, which provides global env."
    )

result = env["product.analytic"]._run_analog_rollup_diagnostic(
    **analog_rollup_diagnostic.configuration_from_environment()
)
for message in result["log_messages"]:
    print(message)
