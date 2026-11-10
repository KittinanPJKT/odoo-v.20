import logging
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

_logger = logging.getLogger(__name__)


def _get_http_request():
    try:
        if request and getattr(request, 'httprequest', None):
            return request
    except RuntimeError:
        return None
    return None


class ReportPrintLog(models.Model):
    _name = 'report.print.log'
    _description = 'Report Print Audit Log'
    _order = 'printed_at desc, id desc'

    printed_at = fields.Datetime(
        string='Printed At',
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        index=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Printed By',
        ondelete='set null',
        readonly=True,
        index=True,
    )
    actor_name = fields.Char(
        string='User / Actor',
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        ondelete='set null',
        readonly=True,
        index=True,
    )
    res_model = fields.Char(
        string='Document Model',
        readonly=True,
        index=True,
    )
    model_name = fields.Char(
        string='Document Type',
        readonly=True,
    )
    res_id = fields.Integer(
        string='Document ID',
        readonly=True,
        index=True,
    )
    res_name = fields.Char(
        string='Document Number',
        readonly=True,
        index=True,
    )
    report_id = fields.Many2one(
        'ir.actions.report',
        string='Report Action',
        ondelete='set null',
        readonly=True,
        index=True,
    )
    report_name = fields.Char(
        string='Report Technical Name',
        readonly=True,
    )
    report_label = fields.Char(
        string='Report / Menu Printed',
        required=True,
        readonly=True,
        index=True,
    )
    action_type = fields.Selection(
        [
            ('print', 'Print'),
            ('download', 'Download'),
        ],
        string='Action',
        required=True,
        default='print',
        readonly=True,
        index=True,
    )
    channel = fields.Selection(
        [
            ('report_preview', 'Print Preview Modal'),
            ('report_menu', 'Print Menu / Button'),
            ('send_download', 'Send & Print Download'),
            ('portal_download', 'Portal Preview Download'),
        ],
        string='Print Channel',
        required=True,
        default='report_menu',
        readonly=True,
        index=True,
    )
    output_format = fields.Char(
        string='Format',
        default='PDF',
        readonly=True,
    )
    print_sequence = fields.Integer(
        string='Print #',
        required=True,
        default=1,
        readonly=True,
    )
    batch_size = fields.Integer(
        string='Batch Count',
        required=True,
        default=1,
        readonly=True,
    )

    def _compute_display_name(self):
        for rec in self:
            doc = rec.res_name or rec.report_label or _('Print Log')
            rec.display_name = f"{doc} - {rec.report_label} (#{rec.print_sequence})"

    def action_open_document(self):
        self.ensure_one()
        if not self.res_model or not self.res_id or self.res_model not in self.env:
            raise UserError(_('ไม่มีเอกสารอ้างอิงสำหรับรายการนี้'))
        record = self.env[self.res_model].browse(self.res_id).exists()
        if not record:
            raise UserError(_('ไม่พบเอกสารต้นทาง (%s ID %s) อาจถูกลบออกจากระบบแล้ว') % (self.res_model, self.res_id))
        record.check_access('read')
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'current',
        }

    @api.model
    def _log_print_events(
        self,
        records=None,
        *,
        report=None,
        report_label=None,
        report_name=None,
        action_type=None,
        channel='report_menu',
        output_format='PDF',
        batch_size=None,
        skip_access_check=False,
        actor_partner=None,
    ):
        """Record print/download audit entries and post internal notes into record Chatter."""
        if self.env.cr.readonly:
            return self.browse()

        if action_type not in ('print', 'download'):
            action_type = 'download' if channel in ('send_download', 'portal_download') else 'print'

        user = self.env.user
        is_public = user._is_public()
        if not is_public:
            actor_user_id = user.id
            actor_name = user.name
            author_id = user.partner_id.id
        else:
            actor_user_id = False
            partner = actor_partner
            actor_name = (
                f"{partner.name} (Portal Link)" if partner and partner.name else _('Portal / Public User')
            )
            author_id = partner.id if partner else self.env.ref('base.partner_root').id

        resolved_report_name = report_name or (report.report_name if report else False) or ''
        resolved_report_label = (
            report_label
            or (report.name if report else False)
            or resolved_report_name
            or _('Document PDF')
        )
        resolved_report_id = report.id if report else False
        now_dt = fields.Datetime.now()
        action_verb = 'Downloaded' if action_type == 'download' else 'Printed'

        # Global report without specific target records (e.g. summary PDF without docids)
        if not records:
            res_model = report.model if report else False
            model_rec = self.env['ir.model'].sudo()._get(res_model) if res_model else False
            model_label = model_rec.name if model_rec else (res_model or '')
            return self.sudo().create([{
                'printed_at': now_dt,
                'user_id': actor_user_id,
                'actor_name': actor_name,
                'company_id': self.env.company.id,
                'res_model': res_model or '',
                'model_name': model_label,
                'res_id': 0,
                'res_name': resolved_report_label,
                'report_id': resolved_report_id,
                'report_name': resolved_report_name,
                'report_label': resolved_report_label,
                'action_type': action_type,
                'channel': channel,
                'output_format': output_format,
                'print_sequence': 1,
                'batch_size': batch_size or 1,
            }])

        records = records.exists()
        if not records:
            return self.browse()

        if not skip_access_check:
            records.check_access('read')

        res_model = records._name
        model_rec = self.env['ir.model'].sudo()._get(res_model)
        model_label = model_rec.name if model_rec else res_model
        effective_batch = batch_size or len(records)

        existing_counts = dict(
            self.sudo()._read_group(
                [('res_model', '=', res_model), ('res_id', 'in', records.ids)],
                ['res_id'],
                ['__count'],
            )
        )

        has_chatter = 'message_ids' in records._fields and hasattr(records, '_message_log')
        create_vals_list = []
        bus_targets = []

        for record in records:
            next_seq = existing_counts.get(record.id, 0) + 1
            existing_counts[record.id] = next_seq

            rec_company = False
            if 'company_id' in record._fields and record.company_id:
                rec_company = record.company_id
            elif self.env.company:
                rec_company = self.env.company

            create_vals_list.append({
                'printed_at': now_dt,
                'user_id': actor_user_id,
                'actor_name': actor_name,
                'company_id': rec_company.id if rec_company else False,
                'res_model': res_model,
                'model_name': model_label,
                'res_id': record.id,
                'res_name': record.display_name or str(record.id),
                'report_id': resolved_report_id,
                'report_name': resolved_report_name,
                'report_label': resolved_report_label,
                'action_type': action_type,
                'channel': channel,
                'output_format': output_format,
                'print_sequence': next_seq,
                'batch_size': effective_batch,
            })

            if has_chatter:
                body = Markup('<div><p>%s: %s</p></div>') % (action_verb, resolved_report_label)
                try:
                    record.sudo()._message_log(
                        body=body,
                        author_id=author_id,
                    )
                    bus_targets.append({'model': res_model, 'id': record.id})
                except Exception:
                    _logger.exception(
                        "Failed to post print audit log to chatter for %s(%s)",
                        res_model,
                        record.id,
                    )

        logs = self.sudo().create(create_vals_list)

        if bus_targets and not is_public and user._is_internal():
            try:
                user._bus_send(
                    'custom_invoice_report/print_logged',
                    {'records': bus_targets[:50]},
                )
            except Exception:
                _logger.debug("Unable to push print audit bus notification", exc_info=True)

        return logs


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        res = super()._render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
        try:
            http_req = _get_http_request()
            if (
                not self.env.cr.readonly
                and not self.env.context.get('skip_portal_render_print_log')
                and data
                and data.get('report_type') == 'pdf'
                and http_req
                and http_req.httprequest.path.startswith('/my/')
                and http_req.httprequest.args.get('download')
            ):
                report = self._get_report(report_ref)
                if report and report.model and res_ids and report.model in self.env:
                    records = self.env[report.model].browse(res_ids).exists()
                    if records:
                        actor_partner = (
                            records[0].partner_id if 'partner_id' in records._fields else None
                        )
                        self.env['report.print.log']._log_print_events(
                            records,
                            report=report,
                            report_name=report.report_name,
                            action_type='download',
                            channel='portal_download',
                            output_format='PDF',
                            batch_size=len(records),
                            skip_access_check=True,
                            actor_partner=actor_partner,
                        )
                        http_req._print_audit_logged = True
        except Exception:
            _logger.exception("Failed to log portal PDF print audit")
        return res
