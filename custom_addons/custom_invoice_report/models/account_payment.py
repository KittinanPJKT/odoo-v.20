from odoo import api, fields, models
from odoo.tools import float_is_zero

from .entronica_report_mixin import _strip_label_prefix


class AccountPayment(models.Model):
    _name = 'account.payment'
    _inherit = ['account.payment', 'entronica.report.mixin']

    # ------------------------------------------------------------------
    # Thai Billing & Receipt Fields
    # ------------------------------------------------------------------
    # ข้อมูลโครงการ งวดงาน และเอกสารอ้างอิง (ดึงจาก Invoice ที่จับคู่เป็นค่าเริ่มต้น แก้ไขเฉพาะใบได้)
    project_name = fields.Char(
        string="Project Name",
        compute="_compute_invoice_billing_info",
        store=True,
        readonly=False,
        help="ชื่อโครงการ เช่น Professional Service (ดึงค่าเริ่มต้นจาก Invoice ที่จับคู่)",
    )
    period = fields.Char(
        string="Period",
        compute="_compute_invoice_billing_info",
        store=True,
        readonly=False,
        help="งวดการทำงาน/รอบการวางบิล เช่น May26 (ดึงค่าเริ่มต้นจาก Invoice ที่จับคู่)",
    )
    po_ref = fields.Char(
        string="Reference / PO",
        compute="_compute_invoice_billing_info",
        store=True,
        readonly=False,
        help="เลขที่ใบสั่งซื้อ / เอกสารอ้างอิง PO เช่น 3000044025 (ดึงจากช่อง Reference / PO ของ Invoice)",
    )

    # สาขาของบริษัทในเอกสาร (ค่าเริ่มต้นจากข้อมูลบริษัท สามารถพิมพ์แก้ไขเฉพาะใบนี้ได้)
    company_branch_name = fields.Char(
        string="Company Branch",
        compute="_compute_company_branch_name",
        store=True,
        readonly=False,
        precompute=True,
        help="สาขาของบริษัทที่แสดงบนใบเสร็จรับเงิน (ค่าเริ่มต้นจากข้อมูลบริษัท สามารถพิมพ์แก้ไขเฉพาะใบนี้ได้)",
    )

    # ตัวเลือกแสดงเลขหน้า (หน้า / Page X/Y)
    show_page_number = fields.Boolean(
        string="Show Page Number",
        default=True,
        help="แสดงข้อความ หน้า / Page X/Y บนหัวใบเสร็จรับเงิน",
    )

    # ข้อมูลการรับชำระเงิน (Payment Received By / รับชำระโดย)
    payment_method_type = fields.Selection(
        [
            ('cash', 'Cash / เงินสด'),
            ('transfer', 'Bank Transfer / เงินโอน'),
            ('cheque', 'Cheque / เช็ค'),
        ],
        string="Receipt Payment Method",
        compute="_compute_payment_method_type",
        store=True,
        readonly=False,
        help="วิธีรับชำระเงินสำหรับแสดงเครื่องหมาย [X] บนใบเสร็จรับเงิน",
    )
    cheque_no = fields.Char(string="Cheque No. / เช็คเลขที่")
    cheque_date = fields.Date(string="Cheque Dated / เช็คลงวันที่")
    bank_branch = fields.Char(string="Bank & Branch / ธนาคาร-สาขา", help="เช่น DB")

    def _get_thai_billing_invoices(self):
        self.ensure_one()
        invoices = self.reconciled_invoice_ids.filtered(lambda m: m.is_invoice(include_receipts=True))
        if not invoices:
            invoices = self.invoice_ids.filtered(lambda m: m.is_invoice(include_receipts=True))
        return invoices.sorted(lambda m: (m.invoice_date or fields.Date.today(), m.id))

    @api.depends(
        'invoice_ids',
        'invoice_ids.project_name',
        'invoice_ids.period',
        'invoice_ids.ref',
        'reconciled_invoice_ids',
        'reconciled_invoice_ids.project_name',
        'reconciled_invoice_ids.period',
        'reconciled_invoice_ids.ref',
        'state',
        'is_reconciled',
        'move_id.line_ids.matched_debit_ids',
        'move_id.line_ids.matched_credit_ids',
    )
    def _compute_invoice_billing_info(self):
        for payment in self:
            invoices = payment._get_thai_billing_invoices()
            if not invoices:
                if not payment._origin:
                    payment.project_name = payment.project_name or False
                    payment.period = payment.period or False
                    payment.po_ref = payment.po_ref or False
                continue
            projects = list(dict.fromkeys(
                (inv.project_name or '').strip() for inv in invoices if (inv.project_name or '').strip()
            ))
            periods = list(dict.fromkeys(
                (inv.period or '').strip() for inv in invoices if (inv.period or '').strip()
            ))
            pos = list(dict.fromkeys(
                (inv.ref or '').strip() for inv in invoices if (inv.ref or '').strip()
            ))
            if projects or not payment._origin:
                payment.project_name = ', '.join(projects) or False
            if periods or not payment._origin:
                payment.period = ', '.join(periods) or False
            if pos or not payment._origin:
                payment.po_ref = ', '.join(pos) or False

    @api.depends('company_id', 'company_id.branch_name')
    def _compute_company_branch_name(self):
        for payment in self:
            company = payment.company_id
            if company:
                payment.company_branch_name = (company.branch_name or '').strip() or False
            else:
                payment.company_branch_name = 'สำนักงานใหญ่'

    @api.depends('journal_id', 'journal_id.type', 'payment_method_line_id', 'payment_method_line_id.code')
    def _compute_payment_method_type(self):
        for payment in self:
            code = (payment.payment_method_line_id.code or '').lower()
            if payment.journal_id and payment.journal_id.type == 'cash':
                payment.payment_method_type = 'cash'
            elif 'check' in code or 'cheque' in code:
                payment.payment_method_type = 'cheque'
            else:
                payment.payment_method_type = 'transfer'

    # ------------------------------------------------------------------
    # Document-specific helpers for Entronica Receipt reports
    # ------------------------------------------------------------------
    def _ent_linked_invoices(self):
        self.ensure_one()
        return self._get_thai_billing_invoices()

    def _ent_doc_number(self):
        """Show matched/reconciled invoice number(s) in Invoice No. / เลขที่ box,
        matching the Excel Receipt template, falling back to payment number."""
        self.ensure_one()
        invoices = self._ent_linked_invoices()
        names = [inv.name for inv in invoices if inv.name and inv.name != '/']
        if names:
            return ', '.join(dict.fromkeys(names))
        return self.name if self.name and self.name != '/' else ''

    def _ent_doc_date(self):
        self.ensure_one()
        return self.date

    def _ent_project_name(self):
        self.ensure_one()
        if self.project_name and self.project_name.strip():
            return self.project_name.strip()
        invoices = self._ent_linked_invoices()
        projects = list(dict.fromkeys(
            (inv.project_name or '').strip() for inv in invoices if (inv.project_name or '').strip()
        ))
        return ', '.join(projects)

    def _ent_vat_rate(self):
        self.ensure_one()
        invoices = self._ent_linked_invoices()
        if not invoices:
            return '0%'
        rates = invoices.mapped('invoice_line_ids.tax_ids').filtered(
            lambda t: t.amount_type == 'percent' and not getattr(t, 'is_withholding_tax', False)
        ).mapped('amount')
        rates = sorted(set(rates))
        return ', '.join('{:g}%'.format(r) for r in rates) or '0%'

    def _ent_wht_rate(self):
        self.ensure_one()
        if getattr(self, 'withhold', 'payment') == 'payment':
            return ''
        lines = getattr(self, 'withholding_line_ids', False)
        if not lines:
            return ''
        rates = sorted({
            abs(wl.tax_id.amount)
            for wl in lines
            if wl.tax_id and wl.tax_id.amount_type == 'percent' and wl.tax_id.amount
        })
        return ', '.join('{:g}%'.format(r) for r in rates)

    def _ent_invoice_allocations(self):
        """Return per-invoice settled breakdown in payment currency and any unallocated amount."""
        self.ensure_one()
        pay_curr = self.currency_id or self.company_id.currency_id
        invoices = self._ent_linked_invoices()
        if not invoices:
            return [], 0.0

        settled_by_inv = {}  # inv_id -> {'inv_curr': float, 'pay_curr': float}
        if self.move_id:
            pay_lines = self.move_id.line_ids.filtered(
                lambda l: l.account_id.account_type in ('asset_receivable', 'liability_payable')
            )
            inv_ids_set = set(invoices.ids)
            for pl in pay_lines:
                for partial in pl.matched_debit_ids:
                    dm = partial.debit_move_id
                    if dm.move_id.id in inv_ids_set:
                        entry = settled_by_inv.setdefault(dm.move_id.id, {'inv_curr': 0.0, 'pay_curr': 0.0})
                        entry['inv_curr'] += partial.debit_amount_currency
                        entry['pay_curr'] += partial.credit_amount_currency
                for partial in pl.matched_credit_ids:
                    cm = partial.credit_move_id
                    if cm.move_id.id in inv_ids_set:
                        entry = settled_by_inv.setdefault(cm.move_id.id, {'inv_curr': 0.0, 'pay_curr': 0.0})
                        entry['inv_curr'] += partial.credit_amount_currency
                        entry['pay_curr'] += partial.debit_amount_currency

        use_draft_fallback = not settled_by_inv
        remaining_budget = self.amount if use_draft_fallback else 0.0

        def _to_pay_curr(inv, amt):
            inv_curr = inv.currency_id or pay_curr
            if inv_curr == pay_curr:
                return amt
            return inv_curr._convert(
                amt,
                pay_curr,
                self.company_id or inv.company_id,
                self.date or fields.Date.context_today(self),
            )

        allocations = []
        total_allocated_pay = 0.0
        for inv in invoices:
            inv_curr = inv.currency_id or pay_curr
            inv_total_in_inv = inv.amount_total
            if use_draft_fallback:
                base_open = inv.amount_residual if (inv.state == 'posted' and inv.amount_residual > 0) else inv.amount_total
                base_pay = _to_pay_curr(inv, base_open)
                if pay_curr.is_zero(remaining_budget) or remaining_budget <= 0:
                    settled_pay = 0.0
                else:
                    settled_pay = min(remaining_budget, base_pay)
                    remaining_budget = pay_curr.round(remaining_budget - settled_pay)
                inv_total_in_pay = _to_pay_curr(inv, inv_total_in_inv)
                if inv_curr == pay_curr:
                    settled_inv = settled_pay
                elif not pay_curr.is_zero(inv_total_in_pay):
                    settled_inv = inv_curr.round(inv_total_in_inv * (settled_pay / inv_total_in_pay))
                else:
                    settled_inv = 0.0
            else:
                entry = settled_by_inv.get(inv.id, {'inv_curr': 0.0, 'pay_curr': 0.0})
                settled_inv = entry['inv_curr']
                settled_pay = entry['pay_curr']

            if pay_curr.is_zero(settled_pay) and inv_curr.is_zero(settled_inv):
                continue

            if inv_curr.is_zero(inv_total_in_inv):
                ratio = 1.0
            else:
                ratio = max(0.0, min(1.0, settled_inv / inv_total_in_inv))

            is_partial = not inv_curr.is_zero(inv_total_in_inv - settled_inv)
            same_curr = (inv_curr == pay_curr)

            if not is_partial and same_curr:
                inv_untaxed = inv.amount_untaxed
                inv_vat = inv.amount_tax
                inv_total = inv.amount_total
            else:
                inv_total = pay_curr.round(settled_pay)
                if not inv_curr.is_zero(inv_total_in_inv):
                    inv_vat = pay_curr.round(inv_total * (inv.amount_tax / inv_total_in_inv))
                else:
                    inv_vat = 0.0
                inv_untaxed = pay_curr.round(inv_total - inv_vat)

            total_allocated_pay = pay_curr.round(total_allocated_pay + inv_total)
            allocations.append({
                'inv': inv,
                'settled_inv': settled_inv,
                'settled_pay': inv_total,
                'ratio': ratio,
                'is_partial': is_partial,
                'same_curr': same_curr,
                'untaxed': inv_untaxed,
                'vat': inv_vat,
                'total': inv_total,
            })

        unapplied = 0.0
        if allocations:
            diff = pay_curr.round(self.amount - total_allocated_pay)
            if diff > 0 and not pay_curr.is_zero(diff):
                unapplied = diff
        return allocations, unapplied

    def _ent_totals(self):
        self.ensure_one()
        pay_curr = self.currency_id or self.company_id.currency_id
        withhold_mode = getattr(self, 'withhold', 'payment')
        raw_wht = (getattr(self, 'withholding_amount', 0.0) or 0.0) if withhold_mode != 'payment' else 0.0
        wht = 0.0 if (not raw_wht or pay_curr.is_zero(raw_wht)) else pay_curr.round(raw_wht)

        allocations, unapplied = self._ent_invoice_allocations()
        if allocations:
            untaxed = pay_curr.round(sum(a['untaxed'] for a in allocations) + unapplied)
            vat = pay_curr.round(sum(a['vat'] for a in allocations))
            total = pay_curr.round(sum(a['total'] for a in allocations) + unapplied)
        else:
            untaxed = self.amount
            vat = 0.0
            total = self.amount

        net_total = pay_curr.round(total - wht)
        return {
            'untaxed': untaxed,
            'vat': vat,
            'total': total,
            'wht': wht,
            'net_total': net_total,
        }

    def _ent_baht_text(self):
        self.ensure_one()
        totals = self._ent_totals()
        target = totals['net_total'] if totals.get('wht') else totals['total']
        return self._ent_amount_words(target)

    def _ent_payment_info(self):
        self.ensure_one()
        method = self.payment_method_type or 'transfer'
        return {
            'is_cash': method == 'cash',
            'is_transfer': method == 'transfer',
            'is_cheque': method == 'cheque',
            'cheque_no': (self.cheque_no or '').strip() if method == 'cheque' else '',
            'cheque_date': (
                self._ent_short_date(self.cheque_date)
                if method == 'cheque' and self.cheque_date
                else ''
            ),
            'bank_branch': (self.bank_branch or '').strip() if method == 'cheque' else '',
        }

    def _ent_report_rows(self):
        self.ensure_one()
        pay_curr = self.currency_id or self.company_id.currency_id
        rows = []
        allocations, unapplied = self._ent_invoice_allocations()
        invoices = self._ent_linked_invoices()
        number = 0
        multi = len(allocations) > 1 or (allocations and unapplied > 0)

        if allocations:
            for alloc in allocations:
                inv = alloc['inv']
                inv_curr = inv.currency_id or pay_curr
                if multi:
                    inv_label = inv.name if inv.name and inv.name != '/' else (inv.display_name or '')
                    if inv_label:
                        rows.append({
                            'kind': 'line_section',
                            'no': '',
                            'desc': f'Invoice No. {inv_label}',
                        })

                inv_lines = inv.invoice_line_ids.sorted('sequence')
                prod_lines = [l for l in inv_lines if l.display_type == 'product']

                # Precompute proportional line subtotals so column AMOUNT sums to alloc['untaxed']
                scaled_subtotals = {}
                if (alloc['is_partial'] or not alloc['same_curr']) and prod_lines:
                    inv_untaxed = inv.amount_untaxed
                    raw_subs = []
                    for pl in prod_lines:
                        if not inv_curr.is_zero(inv_untaxed):
                            val = pay_curr.round(alloc['untaxed'] * (pl.price_subtotal / inv_untaxed))
                        else:
                            val = 0.0
                        raw_subs.append(val)
                    diff = pay_curr.round(alloc['untaxed'] - sum(raw_subs))
                    if diff and not pay_curr.is_zero(diff):
                        raw_subs[-1] = pay_curr.round(raw_subs[-1] + diff)
                    scaled_subtotals = {pl.id: val for pl, val in zip(prod_lines, raw_subs)}

                for line in inv_lines:
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
                    if not alloc['is_partial'] and alloc['same_curr']:
                        price_val = line.price_unit
                        amt_val = line.price_subtotal
                    else:
                        amt_val = scaled_subtotals.get(line.id, 0.0)
                        if alloc['same_curr']:
                            price_val = line.price_unit * alloc['ratio']
                        elif not inv_curr.is_zero(alloc['settled_inv']):
                            price_val = line.price_unit * (alloc['settled_pay'] / alloc['settled_inv'])
                        else:
                            price_val = amt_val / line.quantity if line.quantity else 0.0

                    rows.append({
                        'kind': 'product',
                        'no': number,
                        'desc': text,
                        'qty': self._ent_qty(line.quantity),
                        'price': self._ent_amount(price_val),
                        'amount': self._ent_amount(amt_val),
                    })

                if alloc['is_partial']:
                    pct_text = '{:g}%'.format(round(alloc['ratio'] * 100, 2))
                    paid_str = self._ent_amount(alloc['settled_inv'] if alloc['same_curr'] else alloc['total'])
                    if alloc['same_curr']:
                        orig_str = self._ent_amount(inv.amount_total)
                    else:
                        dp = inv_curr.decimal_places if inv_curr.decimal_places is not None else 2
                        orig_str = f'{inv.amount_total:,.{dp}f} {inv_curr.name}'
                    rows.append({
                        'kind': 'line_note',
                        'no': '',
                        'desc': f'Partial Payment / ชำระบางส่วน : {paid_str} / {orig_str} ({pct_text})',
                    })

            if unapplied > 0:
                number += 1
                rows.append({
                    'kind': 'product',
                    'no': number,
                    'desc': 'Advance / Unallocated Payment / เงินรับล่วงหน้าส่วนเกิน',
                    'qty': '1',
                    'price': self._ent_amount(unapplied),
                    'amount': self._ent_amount(unapplied),
                })
        else:
            desc = (self.memo or '').strip() or 'Payment Receipt / รับชำระเงิน'
            rows.append({
                'kind': 'product',
                'no': 1,
                'desc': desc,
                'qty': '1',
                'price': self._ent_amount(self.amount),
                'amount': self._ent_amount(self.amount),
            })

        period_raw = (self.period or '').strip()
        if not period_raw and invoices:
            period_vals = list(dict.fromkeys(
                _strip_label_prefix(inv.period, 'Period') for inv in invoices
            ))
            period = ', '.join(v for v in period_vals if v)
        else:
            period = _strip_label_prefix(period_raw, 'Period')
        if period:
            rows.append({'kind': 'line_note', 'no': '', 'desc': f'Period : {period}'})

        po_raw = (self.po_ref or '').strip()
        if not po_raw and invoices:
            po_vals = list(dict.fromkeys(
                _strip_label_prefix(inv.ref, 'PO') for inv in invoices
            ))
            ref = ', '.join(v for v in po_vals if v)
        else:
            ref = _strip_label_prefix(po_raw, 'PO')
        if ref:
            rows.append({'kind': 'line_note', 'no': '', 'desc': f'PO : {ref}'})

        return self._ent_compute_row_heights(rows)
