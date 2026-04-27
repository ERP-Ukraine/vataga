{
    'name': 'NBU Currency Rate Update',
    'summary': 'Automatically update currency rates from the NBU API',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'account',
    ],
    'data': [
        'data/ir_cron.xml',
        'views/res_currency_views.xml',
    ],
    'installable': True,
    'application': False,
}
