{
    'name': 'Параметри якості обладнання Vataga',
    'summary': 'Параметри обладнання та налаштування контролю якості',
    'version': '17.0.2.23',
    'category': 'Manufacturing/Quality',
    'author': 'Vataga',
    'license': 'LGPL-3',
    'auto_install': False,
    'installable': True,
    'application': False,
    'depends': [
        'maintenance',
        'quality_control',
        'quality_mrp',
    ],
    'data': [
        'security/technical_board.xml',
        'security/ir.model.access.csv',
        'data/quality_alert_board_stages.xml',
        'data/ir_config_parameter_data.xml',
        'views/quality_equipment_parameter_views.xml',
        'views/maintenance_equipment_views.xml',
        'views/maintenance_equipment_category_views.xml',
        'views/quality_point_views.xml',
        'views/quality_check_views.xml',
        'views/quality_alert_views.xml',
    ],
    'assets': {
        'web.qunit_suite_tests': [
            'quality_vataga/static/tests/board_statusbar_tests.js',
        ],
        'web.assets_backend': [
            'quality_vataga/static/src/fields/board_statusbar.js',
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.js',
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.xml',
            'quality_vataga/static/src/components/measurement_matrix/measurement_matrix.scss',
        ],
    },
}
