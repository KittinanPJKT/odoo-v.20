from odoo import models, fields, api

def num_to_thai_baht(number):
    if number is None:
        return ''
    try:
        number = round(float(number), 2)
    except (ValueError, TypeError):
        return ''

    if number == 0:
        return 'ศูนย์บาทถ้วน'

    is_negative = number < 0
    number = abs(number)

    num_str = f"{number:.2f}"
    integer_part, satang_part = num_str.split('.')
    satang = int(satang_part)

    digits = ['', 'หนึ่ง', 'สอง', 'สาม', 'สี่', 'ห้า', 'หก', 'เจ็ด', 'แปด', 'เก้า']
    units = ['', 'สิบ', 'ร้อย', 'พัน', 'หมื่น', 'แสน']

    def convert_group(group_str):
        group_str = group_str.lstrip('0')
        if not group_str:
            return ''
        length = len(group_str)
        result = []
        for i, ch in enumerate(group_str):
            digit = int(ch)
            pos = length - i - 1
            if digit == 0:
                continue
            if pos == 0 and digit == 1 and length > 1:
                result.append('เอ็ด')
            elif pos == 1 and digit == 2:
                result.append('ยี่สิบ')
            elif pos == 1 and digit == 1:
                result.append('สิบ')
            else:
                result.append(digits[digit] + units[pos])
        return ''.join(result)

    def convert_integer(val_str):
        val_str = val_str.lstrip('0')
        if not val_str:
            return 'ศูนย์'
        groups = []
        while val_str:
            groups.append(val_str[-6:])
            val_str = val_str[:-6]

        result = ''
        for i, g in enumerate(groups):
            w = convert_group(g)
            if w:
                result = w + ('ล้าน' * i) + result
        return result or 'ศูนย์'

    baht_text = convert_integer(integer_part) + 'บาท'
    if satang == 0:
        baht_text += 'ถ้วน'
    else:
        baht_text += convert_group(satang_part) + 'สตางค์'

    return ('ลบ' if is_negative else '') + baht_text


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ข้อมูลโครงการและงวดงาน
    project_name = fields.Char(string="Project Name", help="ชื่อโครงการ เช่น Professional Service")
    period = fields.Char(string="Period", help="งวดการทำงาน/รอบการวางบิล เช่น May26")

    # ข้อมูลการชำระเงิน (สำหรับใบเสร็จรับเงิน Receipt)
    payment_method_type = fields.Selection([
        ('cash', 'Cash / เงินสด'),
        ('transfer', 'Bank Transfer / เงินโอน'),
        ('cheque', 'Cheque / เช็ค'),
    ], string="Payment Method", default='transfer')

    cheque_no = fields.Char(string="Cheque No. / เช็คเลขที่")
    cheque_date = fields.Date(string="Cheque Dated / เช็คลงวันที่")
    bank_branch = fields.Char(string="Bank & Branch / ธนาคาร-สาขา", help="เช่น DB")

    # ตัวอักษรจำนวนเงินภาษาไทย (Baht Text)
    amount_total_words_th = fields.Char(
        string="Total (Thai Words)",
        compute="_compute_amount_total_words_th",
        store=True,
        help="จำนวนเงินรวมสุทธิเป็นตัวอักษรภาษาไทย เช่น หนึ่งหมื่นบาทถ้วน"
    )

    @api.depends('amount_total')
    def _compute_amount_total_words_th(self):
        for move in self:
            move.amount_total_words_th = num_to_thai_baht(move.amount_total)

