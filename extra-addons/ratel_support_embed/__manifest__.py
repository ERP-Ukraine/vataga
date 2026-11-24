{
    "name": "Ratel Support",
    "summary": "Екран техпідтримки Ratel Support усередині Odoo",
    "version": "17.0.2.2.0",
    "category": "Services",
    "author": "Ratel Robotics",
    "license": "LGPL-3",
    "depends": ["base_setup", "web", "helpdesk"],
    "external_dependencies": {"python": ["cryptography"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/menu.xml",
        "views/helpdesk_ticket_views.xml",
        "views/ratel_support_reply_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ratel_support_embed/static/src/embed.js",
            "ratel_support_embed/static/src/embed.xml",
            "ratel_support_embed/static/src/embed.scss",
        ],
    },
    "application": True,
    "installable": True,
}
