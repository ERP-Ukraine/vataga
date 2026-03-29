{
    'name': 'Manufacturing for Vataga',
    'summary': 'Manufacturing Customization',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'summary': 'Manufacturing Orders & BOMs',
    'depends': [
        'mrp',
    ],
    'version': '1.1',
    'license': 'LGPL-3',
    'auto_install': True,
    'demo': [],
    'data': [
        'data/mail_message_subtype.xml',
    ],
    'installable': True,
    'application': False,
    'assets': {
        'web.assets_backend': [
            'mrp_vataga/static/src/chatter/bom_chatter.xml',
            'mrp_vataga/static/src/chatter/bom_chatter.js',
            'mrp_vataga/static/src/chatter/bom_chatter.scss',
        ]
    },
}
