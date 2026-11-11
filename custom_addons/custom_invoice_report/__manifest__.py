{
    'name': 'Entronica Invoice Report',
    'version': '1.2',
    'category': 'Accounting/Accounting',
    'summary': 'Custom Thai/English Billing Fields, Invoice, Tax Invoice & Receipt Reports with Print Preview',
    'description': """
Entronica Invoice, Tax Invoice & Receipt Reports
================================================
Combines Thai billing/receipt fields and Entronica QWeb PDF documents:
- Custom Thai billing fields on Invoice, Payment, Company (EN/TH address, Branch, Fax), and Partner (Branch)
- Original / Copy / Original+Copy QWeb PDF layouts for Invoice, Tax Invoice, and Receipt
- Unified Split-View PDF Print Preview, Live Company Layout Customizer, and Print Audit Log
    """,
    'depends': ['account', 'l10n_th', 'mail', 'web', 'portal'],
    'data': [
        'security/ir.access.csv',
        'reports/paperformat.xml',
        'reports/invoice_template.xml',
        'reports/preview_templates.xml',
        'reports/report_action.xml',
        'views/res_company_views.xml',
        'views/res_partner_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'views/report_print_log_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_invoice_report/static/src/scss/print_preview_dialog.scss',
            'custom_invoice_report/static/src/xml/print_preview_dialog.xml',
            'custom_invoice_report/static/src/js/print_preview_dialog.js',
            'custom_invoice_report/static/src/js/print_chatter_refresh.js',
        ],
        'web.report_assets_common': [
            'custom_invoice_report/static/src/scss/invoice_report.scss',
        ],
    },
    'author': 'Entronica Co., Ltd.',
    'license': 'LGPL-3',
    'installable': True,
}
