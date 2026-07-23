{
    'name': 'Параметри якості обладнання Vataga',
    'summary': 'Параметри обладнання та налаштування контролю якості',
    'version': '17.0.1.5',
    'category': 'Manufacturing/Quality',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'maintenance',
        'quality',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/quality_equipment_parameter_views.xml',
        'views/maintenance_equipment_views.xml',
        'views/maintenance_equipment_category_views.xml',
        'views/quality_point_views.xml',
    ],
}
