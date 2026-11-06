from odoo import models, fields

class ResCompany(models.Model):
    _inherit = 'res.company'

    name_en = fields.Char(string="Company Name (EN)", help="ชื่อบริษัทภาษาอังกฤษ เช่น Entronica Co., Ltd.")
    street_en = fields.Char(string="Street (EN)", help="ที่อยู่ภาษาอังกฤษ บรรทัดที่ 1")
    street2_en = fields.Char(string="Street 2 (EN)", help="ที่อยู่ภาษาอังกฤษ บรรทัดที่ 2")
    city_en = fields.Char(string="City (EN)", help="แขวง/เขต/จังหวัด ภาษาอังกฤษ")
    zip_en = fields.Char(string="Zip Code (EN)", help="รหัสไปรษณีย์")
    fax = fields.Char(string="Fax", help="หมายเลขโทรสาร เช่น 0-21634531")

