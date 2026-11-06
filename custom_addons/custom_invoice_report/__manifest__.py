{
    'name': 'Entronica Invoice Report',
    'version': '1.0',
    'category': 'Accounting/Accounting',
    'summary': 'Custom Thai/English invoice (ใบแจ้งหนี้ / ใบวางบิล) PDF layout',
    'depends': ['account', 'l10n_th'],
    'data': [
        'reports/paperformat.xml',
        'reports/invoice_template.xml',
        'reports/report_action.xml',
    ],
    'assets': {
        'web.report_assets_common': [
            'custom_invoice_report/static/src/scss/invoice_report.scss',
        ],
    },
    'author': 'Entronica Co., Ltd.',
    'license': 'LGPL-3',
    'installable': True,
}
