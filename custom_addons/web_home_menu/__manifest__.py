{
    'name': 'Web Home Menu',
    'version': '1.0',
    'category': 'Hidden',
    'summary': 'Full-screen app grid as the home page of the web client',
    'description': """
Replaces the apps dropdown of the web client with a full-screen home page:
a grid of app icons with search over apps and menus.
    """,
    'depends': ['web'],
    'assets': {
        'web.assets_backend': [
            'web_home_menu/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'web_home_menu/static/tests/**/*',
        ],
    },
    'author': 'KittinanPJKT',
    'license': 'LGPL-3',
}
