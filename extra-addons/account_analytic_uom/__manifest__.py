{
    'name' : 'Show UOM Unit Quantity in Analytic Reporting',
    'version': '1.0',
    'category': 'Human Resources/Approvals',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'account',
    ],
    'data': ['views/account_analytic_line_views.xml'],
}
