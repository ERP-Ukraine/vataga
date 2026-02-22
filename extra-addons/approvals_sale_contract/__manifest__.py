{
    'name' : 'Approvals Sale Contract',
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
        'l10n_ua_contract_approvals',
        'account',
    ],
    'data': [
        'views/approval_request_views.xml',
        'views/approval_category_views.xml',
    ],
}
