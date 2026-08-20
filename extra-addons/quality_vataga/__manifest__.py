{
    'name': 'Параметри якості обладнання Vataga',
    'summary': 'Параметри обладнання та налаштування контролю якості',
    'version': '17.0.2.18',
    'category': 'Manufacturing/Quality',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'maintenance',
        'quality_control',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'views/quality_equipment_parameter_views.xml',
        'views/maintenance_equipment_views.xml',
        'views/maintenance_equipment_category_views.xml',
        'views/quality_point_views.xml',
        'views/quality_check_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.js',
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.xml',
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.scss',
        ],
    },
}
