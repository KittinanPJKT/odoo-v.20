from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    branch_name = fields.Char(
        string="Branch",
        default="สำนักงานใหญ่",
        help="ชื่อสาขา เช่น สำนักงานใหญ่, สาขาที่ 00001",
    )

