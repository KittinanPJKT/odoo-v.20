from odoo import models, fields


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
