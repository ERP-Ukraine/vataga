{
    'name': "User Activity Tracker",

    'summary': """This app provides enhanced monitoring of user login/logout status and total login time, ensuring improved reliability and efficiency in tracking user activities. Users can conveniently view and manage their login status and total login time through the updated system interface.""",

    'description': """
       This app offers improved tracking and viewing of user login/logout status and total login time.
    """,

    'author': "Himanjali Intelligent Automation Private Limited",
    
    'company': 'Himanjali Intelligent Automation Private Limited',
    
    'maintainer': 'Himanjali Intelligent Automation Private Limited',
    
    'website': "https://www.himanjali.com/",

    'category': 'Administration',

    'depends': ['base'],

    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/setting.xml',
    ],
    'license': 'LGPL-3',

    'images':['static/description/assets/banner.gif'],
    
    'installable': True,

    'auto_install': False,

    'application': False
}