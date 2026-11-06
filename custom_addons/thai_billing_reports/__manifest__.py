{
    'name': 'Thai Billing Reports & Fields',
    'version': '1.0',
    'category': 'Accounting/Localizations',
    'summary': 'Custom fields and backend support for Thai Invoice, Tax Invoice, and Receipt documents',
    'description': """
Thai Billing Reports & Fields
=============================
Adds required custom fields and helper logic for Thai accounting documents:
- Project Name, Period, Customer PO
- Payment Method, Cheque Number, Cheque Date, Bank & Branch
- Company English name and address
    """,
    'author': 'Antigravity & Team',
    'license': 'LGPL-3',
    'depends': ['account', 'base'],
    'data': [
        'views/account_move_views.xml',
        'views/res_company_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}

