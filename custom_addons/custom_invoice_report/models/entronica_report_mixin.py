import math
import re
import unicodedata

from odoo import models
from odoo.tools import float_is_zero
from odoo.tools.image import binary_to_image, image_apply_opt, image_data_uri

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
NAME_ONE_LINE_CHARS = {'th': 32, 'en': 36}  # company name length that fits one header line
DOC_ONE_LINE_CHARS = 15      # document number length that fits comfortably at standard font size
PROJECT_TWO_LINE_CHARS = 40  # project name length that fits comfortably at standard font size
LOGO_BOX_WIDTH = 34.0        # logo area in the header, see .ent-logo-box
LOGO_BOX_HEIGHT = 13.0
LOGO_MAX_PIXELS = (402, 154)  # logo box at 300 dpi, keeps the PDF light

ITEMS_HEIGHT_LAST = PAGE_BODY_HEIGHT - TOP_HEIGHT - LAST_BOTTOM_HEIGHT
ITEMS_HEIGHT_NEXT = PAGE_BODY_HEIGHT - TOP_HEIGHT - NEXT_BOTTOM_HEIGHT

THAI_DIGITS = ['ศูนย์', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า']
THAI_PLACES = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน']
EN_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
             'August', 'September', 'October', 'November', 'December']

# Document types the layout can print, as (English title, Thai title). A report
# template passes a list of these keys in `doc_types`; several keys print one
# full set of pages per key, in that order (e.g. original then copy).
ENT_DOCUMENT_TITLES = {
    'invoice_original': ('ORIGINAL INVOICE', 'ต้นฉบับใบแจ้งหนี้ / ใบวางบิล'),
    'invoice_copy': ('INVOICE COPY', 'สำเนาใบแจ้งหนี้ / ใบวางบิล'),
    'tax_invoice_original': ('ORIGINAL TAX INVOICE', 'ต้นฉบับใบกำกับภาษี'),
    'tax_invoice_copy': ('TAX INVOICE COPY', 'สำเนาใบกำกับภาษี'),
    'receipt_original': ('ORIGINAL RECEIPT', 'ต้นฉบับใบเสร็จรับเงิน'),
    'receipt_copy': ('RECEIPT COPY', 'สำเนาใบเสร็จรับเงิน'),
    'credit_note_original': ('ORIGINAL CREDIT NOTE', 'ต้นฉบับใบลดหนี้'),
    'credit_note_copy': ('CREDIT NOTE COPY', 'สำเนาใบลดหนี้'),
    'credit_note_tax_original': ('ORIGINAL CREDIT NOTE / TAX INVOICE', 'ต้นฉบับใบลดหนี้ / ใบกำกับภาษี'),
    'credit_note_tax_copy': ('CREDIT NOTE / TAX INVOICE COPY', 'สำเนาใบลดหนี้ / ใบกำกับภาษี'),
}

WORDS_ONE_LINE_CHARS = 52
WORDS_TWO_LINE_CHARS = 76


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


def _strip_label_prefix(value, prefix):
    text = (value or '').strip()
    if not text:
        return ''
    return re.sub(rf'^{re.escape(prefix)}\s*:\s*', '', text, flags=re.IGNORECASE).strip()


class EntronicaReportMixin(models.AbstractModel):
    _name = 'entronica.report.mixin'
    _description = 'Shared Thai Report Layout Helpers for Entronica Forms'

    # ------------------------------------------------------------------
    # Formatting helpers used by the shared QWeb template
    # ------------------------------------------------------------------
    def _ent_amount(self, value):
        """Accounting style: 1,234.00 (currency precision) and '-' for zero, like the Excel form."""
        currency = self.currency_id
        if not value or (currency and currency.is_zero(value)):
            return '-'
        dp = currency.decimal_places if currency and currency.decimal_places is not None else 2
        return f'{value:,.{dp}f}'

    def _ent_currency_code(self):
        self.ensure_one()
        currency = self.currency_id
        if currency and currency.name and currency.name != 'THB':
            return currency.name
        return ''

    def _ent_amount_words(self, amount):
        """Return Thai Baht text for THB, or English ISO-currency words for foreign currencies."""
        currency = self.currency_id
        if not currency or currency.name == 'THB':
            return baht_text(amount)
        if self.env['res.lang'].sudo().search_count([('code', '=', 'en_US'), ('active', '=', True)]):
            currency_ctx = currency.with_context(lang='en_US')
        else:
            currency_ctx = currency
        negative = amount < 0 and not currency.is_zero(amount)
        words = (currency_ctx.amount_to_text(abs(amount)) or '').strip()
        if words:
            words = words[0].upper() + words[1:]
            if negative:
                words = f'Minus {words}'
            return f'{words} ({currency.name})'
        dp = currency.decimal_places if currency.decimal_places is not None else 2
        return f'{currency.name} {amount:,.{dp}f}'

    def _ent_words_class(self, text):
        length = _visible_length(text or '')
        if length > WORDS_TWO_LINE_CHARS:
            return 'ent-words ent-xlong'
        if length > WORDS_ONE_LINE_CHARS:
            return 'ent-words ent-long'
        return 'ent-words'

    def _ent_qty(self, value):
        if not value:
            return ''
        return '{:,.0f}'.format(value) if float(value).is_integer() else '{:,.2f}'.format(value)

    def _ent_long_date(self, date):
        return date and f'{date.day} {EN_MONTHS[date.month - 1]} {date.year}' or ''

    def _ent_short_date(self, date):
        return date and date.strftime('%d/%m/%Y') or ''

    def _ent_is_credit_note(self):
        self.ensure_one()
        return False

    def _ent_wht_rate(self):
        self.ensure_one()
        return ''

    def _ent_payment_info(self):
        self.ensure_one()
        return {}

    def _ent_sign_mode(self, key):
        if key.startswith('tax_invoice_') or key.startswith('credit_note_tax_'):
            return 'tax_invoice'
        if key.startswith('receipt_'):
            return 'receipt'
        return 'invoice'

    def _ent_document_titles(self, doc_types):
        return [{
            'key': key,
            'title_en': ENT_DOCUMENT_TITLES[key][0],
            'title_th': ENT_DOCUMENT_TITLES[key][1],
            'sign_mode': self._ent_sign_mode(key),
        } for key in doc_types]

    def _ent_tax_id(self, vat):
        """0105553003015 -> '0 1055 5300 301 5' (Thai 13-digit tax ID grouping).
        A value typed with dashes, or that is not 13 digits, is printed as typed."""
        digits = ''.join(c for c in (vat or '') if c.isdigit())
        if len(digits) != 13 or '-' in vat:
            return vat or ''
        return f'{digits[0]} {digits[1:5]} {digits[5:9]} {digits[9:12]} {digits[12]}'

    def _ent_branch(self, partner):
        commercial = partner.commercial_partner_id
        branch = (
            getattr(commercial, 'branch_name', False)
            or getattr(partner, 'branch_name', False)
            or ''
        ).strip()
        if branch:
            return branch
        code = commercial._get_additional_identifier('TH_BRANCH_CODE')
        if code and code != '00000':
            return f'สาขาที่ {code}'
        return ''

    def _ent_company_branch(self):
        self.ensure_one()
        if self.company_branch_name:
            return self.company_branch_name.strip()
        if self.company_branch_name is False:
            return ''
        return self._ent_branch(self.company_id.partner_id)

    def _ent_partner_branch(self):
        self.ensure_one()
        return self._ent_branch(self.partner_id)

    def _ent_address_lines(self, partner):
        line1 = ' '.join(filter(None, [partner.street, partner.street2]))
        line2 = ', '.join(filter(None, [partner.city, partner.state_id.name, partner.zip]))
        return [line for line in (line1, line2) if line]

    def _ent_company_logo(self):
        """Company logo scaled up or down to fill the fixed logo box, keeping its
        ratio, as {'src', 'style'}. Empty when the company has no logo of its own."""
        company = self.company_id
        if company.uses_default_logo:
            return {}
        source = company.logo.content
        if source[:1] == b'<':
            # SVG has no pixel size to read: the CSS limits of the box apply
            return {'src': image_data_uri(source), 'style': ''}
        image = binary_to_image(source)
        ratio = min(LOGO_BOX_WIDTH / image.width, LOGO_BOX_HEIGHT / image.height)
        width, height = image.width * ratio, image.height * ratio
        image.thumbnail(LOGO_MAX_PIXELS)
        return {
            'src': image_data_uri(image_apply_opt(image.convert('RGBA'), 'PNG', optimize=True)),
            'style': f'width: {width:.2f}mm; height: {height:.2f}mm; '
                     f'margin-top: {(LOGO_BOX_HEIGHT - height) / 2:.2f}mm;',
        }

    def _ent_company_name_class(self, name, lang):
        """A long company name uses a smaller font, so that it wraps on two lines."""
        long_name = _visible_length(name or '') > NAME_ONE_LINE_CHARS[lang]
        return 'ent-company-name ent-long' if long_name else 'ent-company-name'

    def _ent_doc_num_class(self, doc_num):
        """Shrink long or multi-invoice numbers so Date below stays inside the box."""
        long_num = _visible_length(doc_num or '') > DOC_ONE_LINE_CHARS
        return 'ent-doc-clip ent-long' if long_num else 'ent-doc-clip'

    def _ent_project_name_class(self, name):
        """Shrink very long project names slightly so Date below stays inside the box."""
        long_name = _visible_length(name or '') > PROJECT_TWO_LINE_CHARS
        return 'ent-project-clip ent-long' if long_name else 'ent-project-clip'

    def _ent_company_address_th(self):
        company = self.company_id
        line2 = ' '.join(filter(None, [company.street2, company.city, company.state_id.name, company.zip]))
        return [line for line in (company.street, line2) if line]

    def _ent_company_address_en(self):
        company = self.company_id
        line2 = ' '.join(filter(None, [company.street2_en, company.city_en, company.zip_en]))
        return [line for line in (company.street_en, line2) if line]

    def _ent_company_contact(self, phone_label, fax_label):
        company = self.company_id
        parts = []
        if company.phone:
            parts.append(f'{phone_label} : {company.phone}')
        if company.fax:
            parts.append(f'{fax_label} : {company.fax}')
        return '  '.join(parts)

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def _ent_compute_row_heights(self, rows):
        for row in rows:
            lines = sum(max(1, math.ceil(_visible_length(part) / DESC_CHARS_PER_LINE))
                        for part in row['desc'].split('\n'))
            row['height'] = ROW_PADDING + LINE_HEIGHT * lines
        return rows

    def _ent_report_rows(self):
        return []

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

    def _get_preview_overrides_for_company(self):
        self.ensure_one()
        preview_cfg = self.env.context.get('custom_preview_overrides')
        if not isinstance(preview_cfg, dict):
            return None
        if preview_cfg.get('company_id') != self.company_id.id:
            return None
        return preview_cfg
