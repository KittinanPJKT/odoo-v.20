import re
from markupsafe import Markup

from odoo import models
from odoo.addons.base.models.ir_qweb_fields import nl2br_enclose
from odoo.tools import BinaryBytes, html2plaintext, html_sanitize

# Odoo standard PDF reports that support Print Preview editing
# (Company layout customizer + PDF.js annotation editor).
DEFAULT_EDITABLE_REPORT_NAMES = frozenset({
    'account.report_invoice_with_payments',
    'account.report_invoice',
    'account.report_payment_receipt',
})

STANDARD_DOCUMENT_LAYOUT_KEYS = frozenset({
    'web.external_layout_standard',
    'web.external_layout_bubble',
    'web.external_layout_wave',
    'web.external_layout_folder',
    'web.external_layout_center',
    'web.external_layout_dual',
    'web.external_layout_lines',
    'web.external_layout_compact',
})


class CompanyPreviewProxy:
    """In-memory read-only overlay on res.company for non-destructive QWeb PDF preview.

    Preserves the underlying record's `id`, `_fields`, `env`, and ORM helpers so QWeb
    directives like `t-field="company.company_details"` and SCSS compilation via
    `web.styles_company_report` work without mutating res.company or ir.attachment.
    """

    def __init__(self, company, overrides=None):
        object.__setattr__(self, '_record', company)
        object.__setattr__(self, '_overrides', dict(overrides or {}))

    def __bool__(self):
        return bool(self._record)

    def __len__(self):
        return len(self._record)

    def __iter__(self):
        yield self

    def ensure_one(self):
        self._record.ensure_one()
        return self

    def sudo(self, flag=True):
        return CompanyPreviewProxy(self._record.sudo(flag), self._overrides)

    def with_context(self, *args, **kwargs):
        return CompanyPreviewProxy(self._record.with_context(*args, **kwargs), self._overrides)

    def with_company(self, company):
        return CompanyPreviewProxy(self._record.with_company(company), self._overrides)

    def with_env(self, env):
        return CompanyPreviewProxy(self._record.with_env(env), self._overrides)

    def has_access(self, operation):
        return self._record.has_access(operation)

    def __getitem__(self, key):
        if key in self._overrides:
            return self._overrides[key]
        return self._record[key]

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._record, name)

    def __repr__(self):
        return f"CompanyPreviewProxy({self._record!r}, keys={list(self._overrides.keys())!r})"


def build_company_preview_overrides(env, company, raw_overrides):
    """Build sanitized, type-safe override values for CompanyPreviewProxy from raw JSON dict."""
    if not isinstance(raw_overrides, dict):
        return {}

    overrides = {}

    if 'report_layout_id' in raw_overrides and raw_overrides['report_layout_id']:
        try:
            rl_id = int(raw_overrides['report_layout_id'])
        except (TypeError, ValueError):
            rl_id = 0
        if rl_id > 0:
            layout = env['report.layout'].sudo().browse(rl_id).exists()
            if layout and layout.view_id:
                overrides['external_report_layout_id'] = layout.view_id

    if 'report_tables_id' in raw_overrides:
        valid_tables = {
            k for k, _ in env['res.company']._fields['report_tables_id'].selection
        }
        val = raw_overrides['report_tables_id']
        if val in valid_tables:
            overrides['report_tables_id'] = val

    if 'font' in raw_overrides:
        valid_fonts = {k for k, _ in env['res.company']._fields['font'].selection}
        val = raw_overrides['font']
        if val in valid_fonts:
            overrides['font'] = val

    for color_field in ('primary_color', 'secondary_color'):
        if color_field in raw_overrides:
            val = raw_overrides[color_field]
            if not val:
                overrides[color_field] = False
            elif isinstance(val, str) and len(val) == 7 and val.startswith('#') and all(
                c in '0123456789abcdefABCDEF' for c in val[1:]
            ):
                overrides[color_field] = val

    if 'logo' in raw_overrides:
        logo_val = raw_overrides['logo']
        if not logo_val:
            overrides['logo'] = False
        elif isinstance(logo_val, BinaryBytes):
            overrides['logo'] = logo_val
        elif isinstance(logo_val, str):
            import base64
            try:
                raw_bytes = base64.b64decode(logo_val, validate=True)
                if raw_bytes:
                    overrides['logo'] = BinaryBytes(raw_bytes)
            except Exception:
                pass

    for html_field in ('company_details', 'report_header', 'report_footer'):
        if html_field in raw_overrides:
            val = raw_overrides[html_field]
            if val is False or val is None or not (isinstance(val, str) and val.strip()):
                overrides[html_field] = False
            elif '<' in val or '>' in val:
                sanitized = html_sanitize(val, sanitize_attributes=True)
                overrides[html_field] = Markup(sanitized) if html2plaintext(sanitized or '').strip() else False
            else:
                overrides[html_field] = nl2br_enclose(val.strip(), 'div')
            if html_field == 'company_details':
                text_val = html2plaintext(overrides[html_field] or '')
                overrides['is_company_details_empty'] = not text_val.strip()

    if 'vat' in raw_overrides:
        val = raw_overrides['vat']
        overrides['vat'] = (str(val).strip() or False) if val is not None else False

    return overrides


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _get_preview_css_markup(self):
        css = self.env.context.get('custom_preview_css')
        if not css or not isinstance(css, str):
            return ''
        return Markup(re.sub(r'</', '', css))

    def _with_preview_overrides(self):
        self.ensure_one()
        preview_cfg = self.env.context.get('custom_preview_overrides')
        if not isinstance(preview_cfg, dict):
            return self
        if preview_cfg.get('company_id') != self.id:
            return self
        overrides = build_company_preview_overrides(
            self.env, self, preview_cfg.get('company_fields') or {}
        )
        if not overrides:
            return self
        return CompanyPreviewProxy(self, overrides)


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def get_paperformat(self):
        preview_pf_id = self.env.context.get('custom_preview_paperformat_id')
        if (
            preview_pf_id
            and len(self) == 1
            and not self.paperformat_id
            and self.report_name in DEFAULT_EDITABLE_REPORT_NAMES
        ):
            try:
                pf_id = int(preview_pf_id)
            except (TypeError, ValueError):
                pf_id = 0
            if pf_id > 0:
                pf = self.env['report.paperformat'].sudo().browse(pf_id).exists()
                if pf:
                    return pf
        return super().get_paperformat()

    def get_paperformat_by_xmlid(self, xml_id):
        preview_pf_id = self.env.context.get('custom_preview_paperformat_id')
        if preview_pf_id and xml_id:
            rep = self.env.ref(xml_id, raise_if_not_found=False)
            if (
                rep
                and rep._name == 'ir.actions.report'
                and not rep.paperformat_id
                and rep.report_name in DEFAULT_EDITABLE_REPORT_NAMES
            ):
                try:
                    pf_id = int(preview_pf_id)
                except (TypeError, ValueError):
                    pf_id = 0
                if pf_id > 0:
                    pf = self.env['report.paperformat'].sudo().browse(pf_id).exists()
                    if pf:
                        return pf
        return super().get_paperformat_by_xmlid(xml_id)
