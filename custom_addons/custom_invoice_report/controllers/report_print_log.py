import base64
import json
import logging
from markupsafe import Markup
import werkzeug.exceptions
from werkzeug.urls import url_parse

from odoo import _, http
from odoo.addons.account.controllers.download_docs import AccountDocumentDownloadController
from odoo.addons.account.controllers.portal import PortalAccount
from odoo.addons.base.models.assetsbundle import ScssStylesheetAsset
from odoo.addons.base.models.ir_qweb_fields import nl2br_enclose
from odoo.addons.web.controllers.report import ReportController
from odoo.exceptions import AccessError, UserError
from odoo.http import request, route
from odoo.http.dispatcher import serialize_exception
from odoo.http.stream import content_disposition
from odoo.tools import OrderedSet, html2plaintext, html_sanitize
from odoo.tools.misc import html_escape
from odoo.tools.safe_eval import safe_eval, time

from ..models.preview_company_proxy import (
    CompanyPreviewProxy,
    DEFAULT_EDITABLE_REPORT_NAMES,
    build_company_preview_overrides,
)

_logger = logging.getLogger(__name__)

_STYLE_AFFECTING_FIELDS = frozenset({
    'report_layout_id',
    'report_tables_id',
    'font',
    'primary_color',
    'secondary_color',
})

_DEFAULT_HEX_COLOR = '#212529'


def _normalize_hex_color(value, fallback=_DEFAULT_HEX_COLOR):
    if (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith('#')
        and all(c in '0123456789abcdefABCDEF' for c in value[1:])
    ):
        return value.lower()
    return fallback


def _coerce_report_html(value):
    """Sanitize HTML strings or convert plain multiline text to safe report HTML."""
    if value is False or value is None:
        return False
    if not isinstance(value, str):
        return False
    if not value.strip():
        return False
    if '<' in value or '>' in value:
        sanitized = html_sanitize(value, sanitize_attributes=True)
        return Markup(sanitized) if html2plaintext(sanitized or '').strip() else False
    return nl2br_enclose(value.strip(), 'div')


class ReportPrintLogController(ReportController):

    @staticmethod
    def _is_report_editable(report_name):
        return (report_name or '') in DEFAULT_EDITABLE_REPORT_NAMES

    @staticmethod
    def _can_customize_layout():
        user = request.env.user
        return bool(
            request.env.is_admin()
            or user.has_group('account.group_account_manager')
            or user.has_group('account.group_account_user')
            or user.has_group('account.group_account_basic')
        )

    @staticmethod
    def _clean_record_ids(raw_ids, fallback_res_id=None, allow_fallback=False):
        ids = []
        if isinstance(raw_ids, (list, tuple)):
            for x in raw_ids:
                if isinstance(x, int) and x > 0:
                    ids.append(x)
                elif isinstance(x, str) and x.isdigit() and int(x) > 0:
                    ids.append(int(x))
        if not ids and allow_fallback and fallback_res_id:
            try:
                num_id = int(fallback_res_id)
                if num_id > 0:
                    ids.append(num_id)
            except (TypeError, ValueError):
                pass
        return list(OrderedSet(ids))

    def _resolve_target_company(self, model='', res_ids=None, company_id=0):
        Company = request.env['res.company']
        clean_ids = self._clean_record_ids(res_ids)
        if model in ('account.move', 'account.payment') and clean_ids:
            records = request.env[model].browse(clean_ids).exists()
            if not records:
                return Company
            records.check_access('read')
            companies = records.company_id
            if len(companies) != 1:
                return Company
            target = companies
        elif company_id:
            try:
                cid = int(company_id)
            except (TypeError, ValueError):
                return Company
            target = Company.browse(cid).exists()
        else:
            target = request.env.company

        if not target or target.id not in request.env.companies.ids:
            return Company
        return target

    def _serialize_layout_state(self, company, from_invoice=True):
        lang = request.env.user.lang
        company_sudo = company.sudo()

        layouts = (
            request.env['report.layout']
            .sudo()
            .with_context(lang=lang)
            .search([], order='sequence, id')
        )
        layout_options = [
            {'id': rl.id, 'name': rl.name, 'key': rl.view_id.key}
            for rl in layouts
            if rl.view_id
        ]
        current_layout_key = (
            company_sudo.external_report_layout_id.key
            if company_sudo.external_report_layout_id
            else 'web.external_layout_standard'
        )
        matched_layout = next(
            (opt for opt in layout_options if opt['key'] == current_layout_key),
            layout_options[0] if layout_options else None,
        )

        company_model = request.env['res.company'].with_context(lang=lang)
        table_options = [
            {'value': k, 'label': v}
            for k, v in company_model._fields['report_tables_id']._description_selection(company_model.env)
        ]
        font_options = [
            {'value': k, 'label': v}
            for k, v in company_model._fields['font']._description_selection(company_model.env)
        ]

        paperformats = (
            request.env['report.paperformat']
            .sudo()
            .with_context(lang=lang)
            .search([], order='id')
        )
        paperformat_options = [
            {'id': pf.id, 'name': pf.name, 'format': pf.format or ''}
            for pf in paperformats
        ]
        default_pf_id = (
            company_sudo.paperformat_id.id
            or (paperformat_options[0]['id'] if paperformat_options else 0)
        )

        logo_b64 = company_sudo.logo.to_base64() if company_sudo.logo else False
        logo_primary, logo_secondary = (
            request.env['base.document.layout']
            .sudo()
            .extract_image_primary_secondary_colors(logo_b64)
            if logo_b64
            else (False, False)
        )

        primary_color = _normalize_hex_color(
            company_sudo.primary_color,
            _normalize_hex_color(logo_primary, _DEFAULT_HEX_COLOR) if company_sudo.primary_color else _DEFAULT_HEX_COLOR,
        )
        secondary_color = _normalize_hex_color(
            company_sudo.secondary_color,
            _normalize_hex_color(logo_secondary, _DEFAULT_HEX_COLOR) if company_sudo.secondary_color else _DEFAULT_HEX_COLOR,
        )

        details_text = (
            html2plaintext(company_sudo.company_details).strip()
            if isinstance(company_sudo.company_details, str) and company_sudo.company_details
            else ''
        )
        details_placeholder = html2plaintext(company_sudo.partner_id.contact_address or '').strip()
        header_text = (
            html2plaintext(company_sudo.report_header).strip()
            if isinstance(company_sudo.report_header, str) and company_sudo.report_header
            else ''
        )
        footer_text = (
            html2plaintext(company_sudo.report_footer).strip()
            if isinstance(company_sudo.report_footer, str) and company_sudo.report_footer
            else ''
        )

        account_number = (
            (company_sudo.partner_id.bank_ids[0].account_number or '')
            if company_sudo.partner_id.bank_ids
            else ''
        )

        return {
            'can_customize': True,
            'company_id': company_sudo.id,
            'company_name': company_sudo.name,
            'from_invoice': bool(from_invoice),
            'layouts': layout_options,
            'table_styles': table_options,
            'fonts': font_options,
            'paperformats': paperformat_options,
            'logo_primary_color': _normalize_hex_color(logo_primary, '') or False,
            'logo_secondary_color': _normalize_hex_color(logo_secondary, '') or False,
            'values': {
                'report_layout_id': matched_layout['id'] if matched_layout else 0,
                'report_tables_id': company_sudo.report_tables_id or 'light',
                'font': company_sudo.font or 'Lato',
                'logo': logo_b64,
                'primary_color': primary_color,
                'secondary_color': secondary_color,
                'company_details': details_text,
                'company_details_raw': str(company_sudo.company_details) if isinstance(company_sudo.company_details, str) else False,
                'company_details_placeholder': details_placeholder,
                'report_header': header_text,
                'report_header_raw': str(company_sudo.report_header) if isinstance(company_sudo.report_header, str) else False,
                'report_footer': footer_text,
                'report_footer_raw': str(company_sudo.report_footer) if isinstance(company_sudo.report_footer, str) else False,
                'paperformat_id': default_pf_id,
                'vat': company_sudo.vat or '',
                'vat_label': company_sudo.country_id.vat_label or _('Tax ID'),
                'account_number': account_number,
                'qr_code': bool(company_sudo.qr_code),
            },
        }

    @route(['/report/preview_layout_state'], type='jsonrpc', auth='user')
    def get_preview_layout_state(self, model='', res_ids=None, company_id=0):
        """Return non-destructive document layout options & current company values."""
        if not self._can_customize_layout():
            return {'can_customize': False, 'company_id': 0}
        company = self._resolve_target_company(model=model, res_ids=res_ids, company_id=company_id)
        if not company:
            return {'can_customize': False, 'company_id': 0}
        return self._serialize_layout_state(company, from_invoice=(model == 'account.move'))

    @route(['/report/extract_logo_colors'], type='jsonrpc', auth='user')
    def extract_preview_logo_colors(self, logo=False):
        """Compute dominant primary/secondary colors from an uploaded logo without saving."""
        if not self._can_customize_layout() or not logo:
            return {'logo_primary_color': False, 'logo_secondary_color': False}
        primary, secondary = (
            request.env['base.document.layout']
            .sudo()
            .extract_image_primary_secondary_colors(logo)
        )
        return {
            'logo_primary_color': _normalize_hex_color(primary, '') or False,
            'logo_secondary_color': _normalize_hex_color(secondary, '') or False,
        }

    @route(['/report/save_document_layout'], type='jsonrpc', auth='user')
    def save_document_layout_values(self, company_id=0, values=None, from_invoice=True):
        """Persist explicitly modified company document layout fields (zero print audit logs)."""
        if not self._can_customize_layout():
            raise AccessError(_("You are not allowed to customize document layouts."))

        company = self._resolve_target_company(company_id=company_id)
        if not company:
            raise AccessError(_("You cannot configure the document layout for this company."))

        if not isinstance(values, dict) or not values:
            return self._serialize_layout_state(company, from_invoice=from_invoice)

        company_sudo = company.sudo()
        company_vals = {}

        if 'report_layout_id' in values and values['report_layout_id']:
            try:
                rl_id = int(values['report_layout_id'])
            except (TypeError, ValueError):
                rl_id = 0
            layout = request.env['report.layout'].sudo().browse(rl_id).exists() if rl_id > 0 else False
            if layout and layout.view_id:
                company_vals['external_report_layout_id'] = layout.view_id.id

        if 'report_tables_id' in values:
            valid_tables = {k for k, _ in request.env['res.company']._fields['report_tables_id'].selection}
            if values['report_tables_id'] in valid_tables:
                company_vals['report_tables_id'] = values['report_tables_id']

        if 'font' in values:
            valid_fonts = {k for k, _ in request.env['res.company']._fields['font'].selection}
            if values['font'] in valid_fonts:
                company_vals['font'] = values['font']

        if 'logo' in values:
            logo_val = values['logo']
            if not logo_val:
                company_vals['logo'] = False
            elif isinstance(logo_val, str):
                try:
                    raw_bytes = base64.b64decode(logo_val, validate=True)
                    if not raw_bytes:
                        raise ValueError("Empty logo payload")
                except Exception as exc:
                    raise UserError(_("Invalid company logo image data.")) from exc
                company_vals['logo'] = logo_val

        for color_field in ('primary_color', 'secondary_color'):
            if color_field in values:
                cval = values[color_field]
                if not cval:
                    company_vals[color_field] = False
                else:
                    norm = _normalize_hex_color(cval, None)
                    if norm:
                        company_vals[color_field] = norm

        for html_field in ('company_details', 'report_header', 'report_footer'):
            if html_field in values:
                company_vals[html_field] = _coerce_report_html(values[html_field])

        if 'paperformat_id' in values and values['paperformat_id']:
            try:
                pf_id = int(values['paperformat_id'])
            except (TypeError, ValueError):
                pf_id = 0
            pf = request.env['report.paperformat'].sudo().browse(pf_id).exists() if pf_id > 0 else False
            if pf:
                company_vals['paperformat_id'] = pf.id

        if 'vat' in values:
            vat_val = values['vat']
            company_vals['vat'] = (str(vat_val).strip() or False) if vat_val is not None else False

        if 'qr_code' in values:
            company_vals['qr_code'] = bool(values['qr_code'])

        has_account_number = 'account_number' in values
        clean_acc_num = (str(values.get('account_number') or '').strip()) if has_account_number else None

        existing_acc = (
            (company_sudo.partner_id.bank_ids[0].account_number or '').strip()
            if company_sudo.partner_id.bank_ids
            else ''
        )
        effective_qr = company_vals.get('qr_code', company_sudo.qr_code)
        effective_acc = clean_acc_num if has_account_number else existing_acc
        if effective_qr and not effective_acc:
            raise UserError(_("Please enter a Bank Account number before enabling QR Code."))

        if company_vals:
            company_sudo.write(company_vals)

        if has_account_number and clean_acc_num:
            partner = company_sudo.partner_id
            if partner.bank_ids:
                bank = partner.bank_ids[0]
                if (bank.account_number or '').strip() != clean_acc_num:
                    bank.allow_out_payment = False
                    bank.account_number = clean_acc_num
                    bank.allow_out_payment = True
            else:
                partner.bank_ids += request.env['res.partner.bank'].sudo()._find_or_create_bank_account(
                    account_number=clean_acc_num,
                    partner=partner,
                    allow_company_account_creation=True,
                    company=company_sudo,
                )

        if from_invoice:
            step = request.env.ref('account.onboarding_onboarding_step_base_document_layout', raise_if_not_found=False)
            if step:
                step.sudo().with_company(company_sudo).action_set_just_done()

        return self._serialize_layout_state(company_sudo, from_invoice=from_invoice)

    @route(['/report/preview_available_reports'], type='jsonrpc', auth='user')
    def get_preview_available_reports(self, model=None, res_ids=None, current_report_name=None):
        """Return PDF reports available for quick switching inside PrintPreviewDialog."""
        if not model or model not in request.env:
            return []

        clean_ids = self._clean_record_ids(res_ids)
        bindings = (
            request.env['ir.actions.actions']
            .with_context(lang=request.env.user.lang)
            .get_bindings(model)
        )
        bound_ids = [r['id'] for r in bindings.get('report', []) if r.get('id')]
        bound_reports = (
            request.env['ir.actions.report']
            .sudo()
            .with_context(lang=request.env.user.lang)
            .browse(bound_ids)
            .exists()
        )
        pdf_reports = [
            rep for rep in bound_reports
            if (rep.report_type or '').startswith('qweb-pdf')
        ]

        domain_report_ids = [rep.id for rep in pdf_reports if rep.domain]
        valid_domain_ids = set()
        if domain_report_ids and clean_ids:
            valid_domain_ids = set(
                request.env['ir.actions.report']
                .browse(domain_report_ids)
                .get_valid_action_reports(model, clean_ids)
            )

        items = []
        seen_names = set()
        for rep in pdf_reports:
            if rep.domain and clean_ids and rep.id not in valid_domain_ids:
                continue
            rname = rep.report_name
            if not rname or rname in seen_names:
                continue
            seen_names.add(rname)
            items.append({
                'id': rep.id,
                'name': rep.name,
                'report_name': rname,
                'report_type': rep.report_type,
                'editable': self._is_report_editable(rname),
            })

        if current_report_name and current_report_name not in seen_names:
            cur_report = (
                request.env['ir.actions.report']
                .sudo()
                .with_context(lang=request.env.user.lang)
                ._get_report_from_name(current_report_name)
            )
            if cur_report and (cur_report.report_type or '').startswith('qweb-pdf'):
                items.insert(0, {
                    'id': cur_report.id,
                    'name': cur_report.name,
                    'report_name': cur_report.report_name,
                    'report_type': cur_report.report_type,
                    'editable': self._is_report_editable(cur_report.report_name),
                })
        return items

    def _inject_layout_preview_context(self, reportname, docids, base_ctx_dict, raw_layout_preview):
        ctx = dict(base_ctx_dict or {})
        for key in ('custom_preview_css', 'custom_preview_overrides', 'custom_preview_paperformat_id'):
            ctx.pop(key, None)

        if not raw_layout_preview or not self._is_report_editable(reportname) or not self._can_customize_layout():
            return ctx

        if isinstance(raw_layout_preview, str):
            try:
                lp = json.loads(raw_layout_preview)
            except (ValueError, TypeError):
                return ctx
        elif isinstance(raw_layout_preview, dict):
            lp = raw_layout_preview
        else:
            return ctx

        if not isinstance(lp, dict):
            return ctx

        try:
            company_id = int(lp.get('company_id') or 0)
        except (TypeError, ValueError):
            company_id = 0
        if not company_id or company_id not in request.env.companies.ids:
            return ctx

        company = request.env['res.company'].browse(company_id).exists()
        if not company:
            return ctx

        if docids:
            ids = [int(x) for x in docids.split(',') if x.isdigit()]
            if ids:
                report_obj = request.env['ir.actions.report'].sudo()._get_report_from_name(reportname)
                target_model = (
                    report_obj.model
                    if report_obj and report_obj.model in request.env
                    else 'account.move'
                )
                records = request.env[target_model].browse(ids).exists()
                if not records:
                    return ctx
                records.check_access('read')
                if 'company_id' in records._fields and any(r.company_id.id != company.id for r in records):
                    return ctx

        allowed_company_fields = (
            'report_layout_id',
            'report_tables_id',
            'font',
            'logo',
            'primary_color',
            'secondary_color',
            'company_details',
            'report_header',
            'report_footer',
            'vat',
            'qr_code',
        )
        company_fields = {k: lp[k] for k in allowed_company_fields if k in lp}

        if any(k in company_fields for k in _STYLE_AFFECTING_FIELDS):
            company_sudo = company.sudo()
            proxy_overrides = build_company_preview_overrides(request.env, company_sudo, company_fields)
            proxy_company = CompanyPreviewProxy(company_sudo, proxy_overrides)
            scss = request.env['ir.qweb'].sudo()._render(
                'web.styles_company_report',
                {'company_ids': proxy_company},
                raise_if_not_found=False,
            )
            if scss and str(scss).strip():
                asset = ScssStylesheetAsset(None, inline='// custom_preview')
                compiled_css = asset.compile(scss)
                if compiled_css:
                    ctx['custom_preview_css'] = str(compiled_css)

        if 'paperformat_id' in lp and lp['paperformat_id']:
            try:
                pf_id = int(lp['paperformat_id'])
            except (TypeError, ValueError):
                pf_id = 0
            if pf_id > 0 and request.env['report.paperformat'].sudo().browse(pf_id).exists():
                ctx['custom_preview_paperformat_id'] = pf_id

        overrides_payload = {
            'company_id': company.id,
            'company_fields': company_fields,
        }
        if 'account_number' in lp:
            overrides_payload['account_number'] = str(lp.get('account_number') or '')

        ctx['custom_preview_overrides'] = overrides_payload
        ctx['report_pdf_no_attachment'] = True
        return ctx

    @route(['/report/preview_pdf'], type='http', auth='user')
    # pylint: disable=unused-argument
    def report_preview_pdf(self, data, context=None, token=None, layout_preview=None):
        """Render a PDF inline for the print preview modal without recording any audit log."""
        requestcontent = json.loads(data)
        url, type_ = requestcontent[0], requestcontent[1]
        reportname = '???'
        if type_.startswith('qweb-pdf'):
            type_ = 'qweb-pdf'

        try:
            if type_ != 'qweb-pdf' or '/report/pdf/' not in url:
                raise werkzeug.exceptions.BadRequest('Only PDF reports support print preview.')

            reportname = url.split('/report/pdf/', 1)[1].split('?', 1)[0]
            docids = None
            if '/' in reportname:
                reportname, docids = reportname.split('/', 1)

            base_ctx = json.loads(context or '{}') if context else {}
            if not isinstance(base_ctx, dict):
                base_ctx = {}

            if docids:
                final_ctx = self._inject_layout_preview_context(reportname, docids, base_ctx, layout_preview)
                response = self.report_routes(
                    reportname,
                    docids=docids,
                    converter='pdf',
                    context=json.dumps(final_ctx),
                )
            else:
                query_data = url_parse(url).decode_query(cls=dict)
                merged_ctx = dict(base_ctx)
                if 'context' in query_data:
                    url_ctx = json.loads(query_data.pop('context') or '{}')
                    if isinstance(url_ctx, dict):
                        merged_ctx.update(url_ctx)
                final_ctx = self._inject_layout_preview_context(reportname, None, merged_ctx, layout_preview)
                response = self.report_routes(
                    reportname,
                    converter='pdf',
                    context=json.dumps(final_ctx),
                    **query_data,
                )

            report = request.env['ir.actions.report']._get_report_from_name(reportname)
            filename = "%s.pdf" % ((report.name if report else False) or reportname)
            if docids and report and report.model and report.model in request.env:
                ids = [int(x) for x in docids.split(',') if x.isdigit()]
                obj = request.env[report.model].browse(ids)
                if report.print_report_name and len(obj) == 1:
                    computed_name = safe_eval(report.print_report_name, {'object': obj, 'time': time})
                    if computed_name:
                        filename = "%s.pdf" % computed_name

            response.headers['Content-Disposition'] = content_disposition(filename, 'inline')
            response.headers['Cache-Control'] = 'no-store'
            return response
        except Exception as e:
            _logger.warning("Error while previewing report %s", reportname, exc_info=True)
            se = serialize_exception(e)
            error = {
                'code': 0,
                'message': "Odoo Server Error",
                'data': se,
            }
            res = request.make_response(html_escape(json.dumps(error)))
            raise werkzeug.exceptions.InternalServerError(response=res) from e

    @route(['/report/log_print_action'], type='jsonrpc', auth='user')
    def log_report_print_action(
        self,
        report_url=None,
        report_type='qweb-pdf',
        report_name=None,
        report_label=None,
        action_type='print',
        context=None,
        fallback_model=None,
        fallback_res_id=None,
    ):
        """Record an explicit Print or Download action triggered from the PDF preview modal."""
        action_kind = 'download' if action_type == 'download' else 'print'
        raw_context = json.dumps(context) if isinstance(context, dict) else context

        report = None
        ids = []
        output_fmt = 'PDF'
        resolved_name = report_name

        if report_url:
            parsed = self._extract_report_target(report_url, report_type or 'qweb-pdf', raw_context)
            if parsed:
                report, resolved_name, ids, output_fmt = parsed

        if not report and resolved_name:
            report = request.env['ir.actions.report']._get_report_from_name(resolved_name)

        if not report:
            return {'logged_count': 0, 'targets': []}

        if (
            not ids
            and fallback_model
            and fallback_res_id
            and report.model == fallback_model
        ):
            try:
                num_id = int(fallback_res_id)
                if num_id > 0:
                    ids = [num_id]
            except (TypeError, ValueError):
                pass

        logs, targets = self._write_report_audit_logs(
            report=report,
            report_name=resolved_name or report.report_name,
            report_label=report_label,
            ids=ids,
            output_fmt=output_fmt,
            action_type=action_kind,
            channel='report_preview',
        )
        return {'logged_count': len(logs), 'targets': targets}

    @route(['/report/download'], type='http', auth="user")
    # pylint: disable=unused-argument
    def report_download(self, data, context=None, token=None, readonly=True):
        response = super().report_download(data, context=context, token=token, readonly=readonly)
        if response and response.status_code == 200:
            self._record_print_audit_from_download(data, context)
        return response

    def _extract_report_target(self, url, type_, raw_context=None):
        if type_.startswith('qweb-pdf'):
            is_pdf = True
            pattern = '/report/pdf/'
        elif type_ == 'qweb-text':
            is_pdf = False
            pattern = '/report/text/'
        else:
            return None

        if pattern not in url:
            return None

        report_part = url.split(pattern, 1)[1].split('?', 1)[0]
        docids = None
        if '/' in report_part:
            reportname, docids = report_part.split('/', 1)
        else:
            reportname = report_part

        report = request.env['ir.actions.report']._get_report_from_name(reportname)
        if not report:
            return None

        ids = []
        if docids:
            ids = [int(x) for x in docids.split(',') if x.isdigit()]
        else:
            merged_ctx = {}
            if raw_context:
                try:
                    merged_ctx.update(json.loads(raw_context) or {})
                except (ValueError, TypeError):
                    pass
            query_data = url_parse(url).decode_query(cls=dict)
            if query_data.get('context'):
                try:
                    merged_ctx.update(json.loads(query_data['context']) or {})
                except (ValueError, TypeError):
                    pass
            active_model = merged_ctx.get('active_model')
            if report.model and (not active_model or active_model == report.model):
                raw_ids = merged_ctx.get('active_ids')
                if not raw_ids and merged_ctx.get('active_id'):
                    raw_ids = [merged_ctx['active_id']]
                if isinstance(raw_ids, (list, tuple)):
                    ids = [
                        int(x) for x in raw_ids
                        if isinstance(x, int) or (isinstance(x, str) and x.isdigit())
                    ]

        output_fmt = 'PDF' if is_pdf else 'TEXT'
        return report, reportname, ids, output_fmt

    def _write_report_audit_logs(
        self,
        *,
        report,
        report_name,
        ids,
        output_fmt,
        action_type,
        channel,
        report_label=None,
    ):
        report_ctx = report.with_context(lang=request.env.user.lang)
        effective_label = report_ctx.name or report_label
        targets = []
        if (
            ids
            and report.model
            and report.model in request.env
            and not request.env[report.model].is_transient()
        ):
            records = request.env[report.model].browse(ids).exists()
            logs = request.env['report.print.log']._log_print_events(
                records,
                report=report_ctx,
                report_label=effective_label,
                report_name=report_name,
                action_type=action_type,
                channel=channel,
                output_format=output_fmt,
                batch_size=len(records) or len(ids),
            )
            targets = [{'model': r._name, 'id': r.id} for r in records]
        else:
            logs = request.env['report.print.log']._log_print_events(
                None,
                report=report_ctx,
                report_label=effective_label,
                report_name=report_name,
                action_type=action_type,
                channel=channel,
                output_format=output_fmt,
                batch_size=1,
            )
        return logs, targets

    def _record_print_audit_from_download(self, raw_data, raw_context):
        try:
            requestcontent = json.loads(raw_data)
            if not isinstance(requestcontent, (list, tuple)) or len(requestcontent) < 2:
                return
            url, type_ = requestcontent[0], requestcontent[1]
            parsed = self._extract_report_target(url, type_, raw_context)
            if not parsed:
                return
            report, reportname, ids, output_fmt = parsed
            self._write_report_audit_logs(
                report=report,
                report_name=reportname,
                ids=ids,
                output_fmt=output_fmt,
                action_type='download',
                channel='report_menu',
            )
        except Exception:
            _logger.exception("Failed to log report download print audit")


class AccountDocumentDownloadPrintLogController(AccountDocumentDownloadController):

    @http.route('/account/download_invoice_attachments/<models("ir.attachment"):attachments>', type='http', auth='user')
    def download_invoice_attachments(self, attachments):
        response = super().download_invoice_attachments(attachments)
        try:
            if response and response.status_code == 200 and attachments:
                move_ids = list(OrderedSet(
                    a.res_id for a in attachments if a.res_model == 'account.move' and a.res_id
                ))
                if move_ids:
                    moves = request.env['account.move'].browse(move_ids).exists()
                    label = (
                        f"Send & Download ({attachments.name})"
                        if len(attachments) == 1 and attachments.name
                        else _('Invoice PDF Attachments (Send & Download)')
                    )
                    fmt = 'PDF' if len(attachments) == 1 else 'ZIP'
                    request.env['report.print.log']._log_print_events(
                        moves,
                        report_label=label,
                        report_name='account.download_invoice_attachments',
                        action_type='download',
                        channel='send_download',
                        output_format=fmt,
                        batch_size=len(moves),
                    )
        except Exception:
            _logger.exception("Failed to log invoice attachment download print audit")
        return response

    @http.route('/account/download_invoice_documents/<models("account.move"):invoices>/<string:filetype>', type='http', auth='user')
    def download_invoice_documents_filetype(self, invoices, filetype, allow_fallback=True):
        response = super().download_invoice_documents_filetype(invoices, filetype, allow_fallback=allow_fallback)
        try:
            if response and getattr(response, 'status_code', None) == 200 and invoices:
                fmt = 'ZIP' if (filetype == 'all' or len(invoices) > 1) else filetype.upper()
                request.env['report.print.log']._log_print_events(
                    invoices,
                    report_label=_('Invoice Legal Document (%s)', filetype.upper()),
                    report_name=f'account.download_invoice_documents.{filetype}',
                    action_type='download',
                    channel='send_download',
                    output_format=fmt,
                    batch_size=len(invoices),
                )
        except Exception:
            _logger.exception("Failed to log invoice document download print audit")
        return response

    @http.route('/account/download_move_attachments/<models("account.move"):moves>', type='http', auth='user')
    def download_move_attachments(self, moves):
        response = super().download_move_attachments(moves)
        try:
            if response and getattr(response, 'status_code', None) == 200 and moves:
                request.env['report.print.log']._log_print_events(
                    moves,
                    report_label=_('Invoice Export Documents (ZIP)'),
                    report_name='account.download_move_attachments',
                    action_type='download',
                    channel='send_download',
                    output_format='ZIP',
                    batch_size=len(moves),
                )
        except Exception:
            _logger.exception("Failed to log move attachments zip download print audit")
        return response


class PortalAccountPrintLogController(PortalAccount):

    @http.route(['/my/invoices/<int:invoice_id>'], type='http', auth="public", website=True)
    def portal_my_invoice_detail(self, invoice_id, access_token=None, report_type=None, download=False, **kw):
        response = super().portal_my_invoice_detail(
            invoice_id,
            access_token=access_token,
            report_type=report_type,
            download=download,
            **kw,
        )
        try:
            if (
                report_type == 'pdf'
                and download
                and response
                and getattr(response, 'status_code', None) == 200
                and not getattr(request, '_print_audit_logged', False)
            ):
                invoice_sudo = request.env['account.move'].sudo().browse(invoice_id).exists()
                if invoice_sudo:
                    report = (
                        invoice_sudo.partner_id.invoice_template_pdf_report_id
                        or request.env.ref('account.account_invoices', raise_if_not_found=False)
                    )
                    request.env['report.print.log']._log_print_events(
                        invoice_sudo,
                        report=report,
                        report_label=(report.name if report else _('Portal Invoice PDF')),
                        report_name=(report.report_name if report else 'account.account_invoices'),
                        action_type='download',
                        channel='portal_download',
                        output_format='PDF',
                        batch_size=1,
                        skip_access_check=True,
                        actor_partner=invoice_sudo.partner_id,
                    )
        except Exception:
            _logger.exception("Failed to log portal invoice PDF download audit")
        return response
