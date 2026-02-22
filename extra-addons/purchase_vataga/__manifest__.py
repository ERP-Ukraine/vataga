{
    'name' : 'Purchase Vataga',
    'version': '1.3',
    'category': 'Inventory/Purchase',
    'author': 'ERP Ukraine LLC',
    'website': 'https://erp.co.ua',
    'support': 'support@erp.co.ua',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'purchase',
        'account_vataga',
    ],
    'data': [
        'views/purchase_order.xml',
    ],
    'assets': {},
}
