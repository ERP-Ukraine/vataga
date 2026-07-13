{
    'name': 'Параметри якості обладнання Vataga',
    'summary': 'Параметри якості для обладнання',
    'version': '17.0.1.3',
    'category': 'Manufacturing/Maintenance',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'maintenance',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/quality_equipment_parameter_views.xml',
        'views/maintenance_equipment_views.xml',
        'views/maintenance_equipment_category_views.xml',
    ],
}
