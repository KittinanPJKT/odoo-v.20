import math
import unicodedata

from odoo import models

# Page geometry, in mm. These must match invoice_report.scss: the template draws
# every page as a fixed-height block, so pagination is decided here, not by
# wkhtmltopdf, which is what keeps the totals and the signature boxes together
# on the last page whatever the number of lines.
PAGE_BODY_HEIGHT = 275.0     # A4 297mm - 10mm top - 10mm bottom - 2mm safety
TOP_HEIGHT = 75.0            # company header + title + customer boxes + table head
LAST_BOTTOM_HEIGHT = 72.0    # totals + amount in words + signature boxes
NEXT_BOTTOM_HEIGHT = 8.0     # "continued on next page" strip
ROW_PADDING = 1.2            # vertical padding of one item row
LINE_HEIGHT = 5.0            # height of one text line in an item row
DESC_CHARS_PER_LINE = 44     # conservative width of the description column

ITEMS_HEIGHT_LAST = PAGE_BODY_HEIGHT - TOP_HEIGHT - LAST_BOTTOM_HEIGHT
ITEMS_HEIGHT_NEXT = PAGE_BODY_HEIGHT - TOP_HEIGHT - NEXT_BOTTOM_HEIGHT

THAI_DIGITS = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า']
THAI_PLACES = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน']
EN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
             'August', 'September', 'October', 'November', 'December']

# Document types the layout can print, as (English title, Thai title). A report
# template passes a list of these keys in `doc_types`; several keys print one
# full set of pages per key, in that order (e.g. original then copy).
# Tax invoice / receipt variants will be added here.
ENT_DOCUMENT_TITLES = {
    'invoice_original': ('ORIGINAL INVOICE', 'ต้นฉบับใบแจ้งหนี้ / ใบวางบิล'),
    'invoice_copy': ('INVOICE COPY', 'สำเนาใบแจ้งหนี้ / ใบวางบิล'),
}


def _thai_number(number):
    """Read a non-negative integer in Thai (no unit)."""
    if number == 0:
        return ''
    if number >= 1_000_000:
        millions, rest = divmod(number, 1_000_000)
        return _thai_number(millions) + 'ล้าน' + ('เอ็ด' if rest == 1 else _thai_number(rest))
    words = ''
    digits = str(number)
    size = len(digits)
    for i, char in enumerate(digits):
        digit = int(char)
        place = size - i - 1
        if digit == 0:
            continue
        if place == 0 and digit == 1 and size > 1:
            words += 'เอ็ด'
        elif place == 1 and digit == 2:
            words += 'ยี่'
        elif place == 1 and digit == 1:
            words += ''
        else:
            words += THAI_DIGITS[digit]
        words += THAI_PLACES[place]
    return words


def baht_text(amount):
    """1234.50 -> 'หนึ่งพันสองร้อยสามสิบสี่บาทห้าสิบสตางค์'"""
    negative = amount < 0
    satang_total = int(round(abs(amount) * 100))
    baht, satang = divmod(satang_total, 100)
    if baht == 0 and satang == 0:
        return 'ศูนย์บาทถ้วน'
    text = ''
    if baht:
        text += _thai_number(baht) + 'บาท'
    text += _thai_number(satang) + 'สตางค์' if satang else 'ถ้วน'
    return ('ลบ' if negative else '') + text


def _visible_length(text):
    """Thai vowels and tone marks above/below a consonant take no width."""
    return sum(1 for char in text if not unicodedata.combining(char)
               and unicodedata.category(char) != 'Mn')


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------
    # Formatting helpers used by the QWeb template
    # ------------------------------------------------------------------
    def _ent_amount(self, value):
        """Accounting style: 1,234.00 and '-' for zero, like the Excel form."""
        if not value or self.currency_id.is_zero(value):
            return '-'
        return '{:,.2f}'.format(value)

    def _ent_qty(self, value):
        if not value:
            return ''
        return '{:,.0f}'.format(value) if float(value).is_integer() else '{:,.2f}'.format(value)

    def _ent_long_date(self, date):
        return date and f'{date.day} {EN_MONTHS[date.month - 1]} {date.year}' or ''

    def _ent_short_date(self, date):
        return date and date.strftime('%d/%m/%Y') or ''

    def _ent_baht_text(self):
        return baht_text(self.amount_total)

    def _ent_document_titles(self, doc_types):
        return [{'title_en': ENT_DOCUMENT_TITLES[key][0], 'title_th': ENT_DOCUMENT_TITLES[key][1]}
                for key in doc_types]

    def _ent_tax_id(self, vat):
        """0105553003015 -> '0 1055 5300 301 5' (Thai 13-digit tax ID grouping)."""
        digits = ''.join(c for c in (vat or '') if c.isdigit())
        if len(digits) != 13:
            return vat or ''
        return f'{digits[0]} {digits[1:5]} {digits[5:10]} {digits[10:12]} {digits[12]}'

    def _ent_branch(self, partner):
        partner = partner.commercial_partner_id
        if not partner.is_company:
            return ''
        code = partner._get_additional_identifier('TH_BRANCH_CODE')
        if code and code != '00000':
            return f'สาขาที่ {code}'
        return 'สำนักงานใหญ่'

    def _ent_address_lines(self, partner):
        line1 = ' '.join(filter(None, [partner.street, partner.street2]))
        line2 = ', '.join(filter(None, [partner.city, partner.state_id.name, partner.zip]))
        return [line for line in (line1, line2) if line]

    def _ent_vat_rate(self):
        rates = self.invoice_line_ids.tax_ids.filtered(
            lambda t: t.amount_type == 'percent').mapped('amount')
        rates = sorted(set(rates))
        return ', '.join('{:g}%'.format(r) for r in rates) or '0%'

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
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
            number += 1
            rows.append({
                'kind': 'product',
                'no': number,
                'desc': text,
                'qty': self._ent_qty(line.quantity),
                'price': self._ent_amount(line.price_unit),
                'amount': self._ent_amount(line.price_subtotal),
            })
        if self.ref:
            rows.append({'kind': 'line_note', 'no': '', 'desc': f'PO : {self.ref}'})
        for row in rows:
            lines = sum(max(1, math.ceil(_visible_length(part) / DESC_CHARS_PER_LINE))
                        for part in row['desc'].split('\n'))
            row['height'] = ROW_PADDING + LINE_HEIGHT * lines
        return rows

    def _ent_report_pages(self):
        """Split the rows in pages. Only the last page carries the totals and the
        signature boxes, the other ones get a taller item area instead."""
        self.ensure_one()
        rows = self._ent_report_rows()
        pages = []
        start = 0
        while True:
            remaining = rows[start:]
            if sum(r['height'] for r in remaining) <= ITEMS_HEIGHT_LAST:
                pages.append(remaining)
                break
            used, end = 0.0, start
            while end < len(rows) and used + rows[end]['height'] <= ITEMS_HEIGHT_NEXT:
                used += rows[end]['height']
                end += 1
            # A single row taller than the whole area still has to go somewhere,
            # and the last page must keep at least one row with the totals.
            if end == start:
                end = start + 1
            if end == len(rows):
                end -= 1
            pages.append(rows[start:end])
            start = end
        total = len(pages)
        return [{
            'rows': page_rows,
            'number': index + 1,
            'total': total,
            'is_last': index + 1 == total,
            'items_height': ITEMS_HEIGHT_LAST if index + 1 == total else ITEMS_HEIGHT_NEXT,
        } for index, page_rows in enumerate(pages)]
