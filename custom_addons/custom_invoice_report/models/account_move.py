from odoo import api, fields, models
from odoo.tools import float_is_zero

from .entronica_report_mixin import (
    DESC_CHARS_PER_LINE,
    EN_MONTHS,
    ENT_DOCUMENT_TITLES,
    ITEMS_HEIGHT_LAST,
    ITEMS_HEIGHT_NEXT,
    LAST_BOTTOM_HEIGHT,
    LINE_HEIGHT,
    LOGO_BOX_HEIGHT,
    LOGO_BOX_WIDTH,
    LOGO_MAX_PIXELS,
    NAME_ONE_LINE_CHARS,
    NEXT_BOTTOM_HEIGHT,
    PAGE_BODY_HEIGHT,
    PROJECT_TWO_LINE_CHARS,
    ROW_PADDING,
    THAI_DIGITS,
    THAI_PLACES,
    TOP_HEIGHT,
    _strip_label_prefix,
    _thai_number,
    _visible_length,
    baht_text,
)

# Odoo's standard sample QR code image for Print Preview when QR Code is toggled on
# before a valid PromptPay / SEPA / EMV bank proxy has been saved on the company.
PREVIEW_SAMPLE_QR_URI = (
    "data:image/png;base64,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"
)


class AccountMove(models.Model):
    _name = 'account.move'
    _inherit = ['account.move', 'entronica.report.mixin']

    # ------------------------------------------------------------------
    # Thai Billing & Payment Fields
    # ------------------------------------------------------------------
    # ข้อมูลโครงการและงวดงาน
    project_name = fields.Char(string="Project Name", help="ชื่อโครงการ เช่น Professional Service")
    period = fields.Char(string="Period", help="งวดการทำงาน/รอบการวางบิล เช่น May26")

    # สาขาของบริษัทในเอกสาร (ค่าเริ่มต้นจากข้อมูลบริษัท สามารถพิมพ์แก้ไขเฉพาะใบนี้ได้)
    company_branch_name = fields.Char(
        string="Company Branch",
        compute="_compute_company_branch_name",
        store=True,
        readonly=False,
        precompute=True,
        help="สาขาของบริษัทที่แสดงบนเอกสาร (ค่าเริ่มต้นจากข้อมูลบริษัท สามารถพิมพ์แก้ไขเฉพาะใบนี้ได้)",
    )

    # ตัวเลือกแสดงเลขหน้า (หน้า / Page X/Y)
    show_page_number = fields.Boolean(
        string="Show Page Number",
        default=True,
        help="แสดงข้อความ หน้า / Page X/Y บนหัวเอกสาร",
    )

    @api.depends('company_id', 'company_id.branch_name')
    def _compute_company_branch_name(self):
        for move in self:
            company = move.company_id
            if company:
                move.company_branch_name = (company.branch_name or '').strip() or False
            else:
                move.company_branch_name = 'สำนักงานใหญ่'

    # ------------------------------------------------------------------
    # Document-specific helpers used by the shared QWeb template
    # ------------------------------------------------------------------
    def _ent_doc_number(self):
        self.ensure_one()
        return self.name if self.name and self.name != '/' else ''

    def _ent_doc_date(self):
        self.ensure_one()
        return self.invoice_date

    def _ent_project_name(self):
        self.ensure_one()
        return (self.project_name or '').strip()

    def _ent_totals(self):
        self.ensure_one()
        return {
            'untaxed': self.amount_untaxed,
            'vat': self.amount_tax,
            'total': self.amount_total,
        }

    def _ent_is_credit_note(self):
        self.ensure_one()
        return self.move_type == 'out_refund'

    def _ent_document_titles(self, doc_types):
        if len(self) == 1 and self.move_type == 'out_refund':
            credit_map = {
                'invoice_original': 'credit_note_original',
                'invoice_copy': 'credit_note_copy',
                'tax_invoice_original': 'credit_note_tax_original',
                'tax_invoice_copy': 'credit_note_tax_copy',
            }
            doc_types = [credit_map.get(key, key) for key in doc_types]
        return super()._ent_document_titles(doc_types)

    def _ent_baht_text(self):
        self.ensure_one()
        return self._ent_amount_words(self.amount_total)

    def _ent_vat_rate(self):
        rates = self.invoice_line_ids.tax_ids.filtered(
            lambda t: t.amount_type == 'percent' and not getattr(t, 'is_withholding_tax', False)
        ).mapped('amount')
        rates = sorted(set(rates))
        return ', '.join('{:g}%'.format(r) for r in rates) or '0%'

    def _ent_payment_info(self):
        self.ensure_one()
        return {}

    def _ent_report_rows(self):
        rows = []
        number = 0
        for line in self.invoice_line_ids.sorted('sequence'):
            text = line.name or line.product_id.display_name or ''
            if line.display_type in ('line_section', 'line_subsection', 'line_note'):
                rows.append({'kind': line.display_type, 'no': '', 'desc': text})
                continue
            if line.display_type != 'product':
                continue
            if line.discount and not float_is_zero(line.discount, precision_digits=2):
                disc_note = f'(ส่วนลด {line.discount:g}% / Discount {line.discount:g}%)'
                text = f'{text}\n{disc_note}' if text else disc_note
            number += 1
            rows.append({
                'kind': 'product',
                'no': number,
                'desc': text,
                'qty': self._ent_qty(line.quantity),
                'price': self._ent_amount(line.price_unit),
                'amount': self._ent_amount(line.price_subtotal),
            })
        if self.move_type == 'out_refund' and self.reversed_entry_id:
            orig = self.reversed_entry_id
            orig_num = orig.name if orig.name and orig.name != '/' else (orig.display_name or '')
            orig_date = self._ent_long_date(orig.invoice_date)
            orig_ref = f'{orig_num} ({orig_date})' if orig_date else orig_num
            if orig_ref:
                rows.append({
                    'kind': 'line_note',
                    'no': '',
                    'desc': f'Ref. Original Invoice / อ้างอิงใบกำกับภาษีเดิม : {orig_ref}',
                })
            if hasattr(self, '_l10n_th_get_credit_debit_note_amounts'):
                cn_amounts = self._l10n_th_get_credit_debit_note_amounts()
                if len(cn_amounts) == 2:
                    dp = self.currency_id.decimal_places if self.currency_id and self.currency_id.decimal_places is not None else 2
                    orig_amt = f"{cn_amounts[0]['amount']:,.{dp}f}"
                    corr_amt = f"{cn_amounts[1]['amount']:,.{dp}f}"
                    rows.append({
                        'kind': 'line_note',
                        'no': '',
                        'desc': f'Original Value / มูลค่าเดิม : {orig_amt} | Correct Value / มูลค่าที่ถูกต้อง : {corr_amt}',
                    })
        period = _strip_label_prefix(self.period, 'Period')
        if period:
            rows.append({'kind': 'line_note', 'no': '', 'desc': f'Period : {period}'})
        if self.move_type == 'out_refund':
            raw_ref = (self.ref or '').strip()
            if raw_ref.lower().startswith('reversal of:'):
                # Format from account.move.reversal wizard is "Reversal of: <name>, <reason>"
                after_colon = raw_ref.split(':', 1)[1].strip()
                reason = after_colon.split(',', 1)[1].strip() if ',' in after_colon else ''
                if reason:
                    rows.append({'kind': 'line_note', 'no': '', 'desc': f'Reason / เหตุผล : {reason}'})
            elif raw_ref:
                rows.append({'kind': 'line_note', 'no': '', 'desc': f'Ref / เหตุผล : {raw_ref}'})
        else:
            ref = _strip_label_prefix(self.ref, 'PO')
            if ref:
                rows.append({'kind': 'line_note', 'no': '', 'desc': f'PO : {ref}'})
        return self._ent_compute_row_heights(rows)

    # ------------------------------------------------------------------
    # Print Flow & Non-Destructive Live Preview Helpers
    # ------------------------------------------------------------------
    def _preview_has_bank_override(self):
        cfg = self._get_preview_overrides_for_company()
        if not cfg or 'account_number' not in cfg:
            return False
        overridden = (cfg.get('account_number') or '').strip()
        current = (self.partner_bank_id.account_number or '').strip() if self.partner_bank_id else ''
        return overridden != current

    def _preview_override_bank_number(self):
        cfg = self._get_preview_overrides_for_company()
        if not cfg or 'account_number' not in cfg:
            return False
        return (cfg.get('account_number') or '').strip() or False

    def _preview_show_qr(self, default_show_qr):
        cfg = self._get_preview_overrides_for_company()
        if not cfg or 'qr_code' not in (cfg.get('company_fields') or {}):
            return default_show_qr
        qr_enabled = bool(cfg['company_fields']['qr_code'])
        return bool(
            qr_enabled
            and self.move_type in ('out_invoice', 'out_receipt', 'in_invoice', 'in_receipt')
            and self.amount_residual
        )

    def _preview_generate_qr_code(self):
        cfg = self._get_preview_overrides_for_company()
        if not cfg or 'qr_code' not in (cfg.get('company_fields') or {}):
            return self._generate_qr_code(silent_errors=True)

        if not cfg['company_fields']['qr_code']:
            return None
        if self.move_type not in ('out_invoice', 'out_receipt', 'in_invoice', 'in_receipt'):
            return None

        # Read-only QR resolution: never mutate self.qr_code_method during preview.
        bank = self.partner_bank_id
        overridden_acc = cfg.get('account_number')
        if overridden_acc is not None and (
            not bank or (bank.account_number or '').strip() != (overridden_acc or '').strip()
        ):
            bank = False

        if bank:
            qr_method = self.qr_code_method
            if qr_method:
                if bank._get_error_messages_for_qr(qr_method, self.partner_id, self.currency_id):
                    qr_method = None
            else:
                for candidate_method, _candidate_name in self.env['res.partner.bank'].get_available_qr_methods_in_sequence():
                    if not bank._get_error_messages_for_qr(candidate_method, self.partner_id, self.currency_id):
                        qr_method = candidate_method
                        break
            if qr_method:
                unstruct_ref = self.payment_reference or self.name
                live_qr = bank.build_qr_code_base64(
                    self.amount_residual,
                    unstruct_ref,
                    self.payment_reference,
                    self.currency_id,
                    self.partner_id,
                    qr_method,
                    silent_errors=True,
                )
                if live_qr:
                    return live_qr

        return PREVIEW_SAMPLE_QR_URI

    def action_print_pdf(self):
        self.ensure_one()
        invoice_template = self.env['account.move.send']._get_default_pdf_report_id(self)
        report_action = invoice_template.report_action(self.id, config=False)
        report_action['id'] = invoice_template.id
        report_ctx = dict(report_action.get('context') or {})
        report_ctx.update({
            'active_model': self._name,
            'active_id': self.id,
            'active_ids': [self.id],
            'skip_layout_configurator': True,
            'discard_logo_check': True,
        })
        report_action['context'] = report_ctx
        return report_action
