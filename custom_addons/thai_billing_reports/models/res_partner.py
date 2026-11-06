from odoo import models, fields

class ResPartner(models.Model):
    _inherit = 'res.partner'

    branch_code = fields.Char(string="Branch Code", default="00000", help="รหัสสาขา 5 หลัก เช่น 00000")
    branch_name = fields.Char(string="Branch Name", default="สำนักงานใหญ่", help="ชื่อสาขา เช่น สำนักงานใหญ่ หรือ สาขาที่ 1")

