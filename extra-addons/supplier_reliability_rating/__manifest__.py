{
    "name": "Supplier Reliability Rating",
    "version": "1.0",
    "category": "Purchases",
    "author": "ERP Ukraine LLC",
    "website": "https://erp.co.ua",
    "support": "support@erp.co.ua",
    "license": "LGPL-3",
    "auto_install": False,
    "installable": True,
    "application": False,
    "depends": [
        "base",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/supplier_reliability_rating_data.xml",
        "views/res_partner_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "supplier_reliability_rating/static/src/scss/supplier_reliability_rating.scss",
        ],
    },
    "post_init_hook": "post_init_hook",
}
