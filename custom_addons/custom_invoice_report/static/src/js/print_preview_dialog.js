import { Component, onWillDestroy, proxy, signal, t, useEffect, useProps } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { Dialog } from "@web/core/dialog/dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { _t } from "@web/core/l10n/translation";
import { downloadFile, parse as parseContentDisposition } from "@web/core/network/download";
import { ConnectionLostError, makeErrorFromResponse, rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { getReportUrl } from "@web/webclient/actions/reports/utils";

export const DEFAULT_EDITABLE_REPORT_NAMES = new Set([
    "account.report_invoice_with_payments",
    "account.report_invoice",
]);

function buildPdfJsToolbarCss(editable) {
    const hiddenSelectors = [
        "button#openFile",
        "button#secondaryOpenFile",
        "a#viewBookmark",
        "a#secondaryViewBookmark",
        "button#downloadButton",
        "button#secondaryDownload",
        "button#printButton",
        "button#secondaryPrint",
    ];
    if (!editable) {
        hiddenSelectors.push("#editorModeButtons", "#editorModeSeparator");
    }
    return `
        ${hiddenSelectors.join(",\n        ")} {
            display: none !important;
        }
    `;
}

function cloneLayoutValues(vals = {}) {
    return {
        report_layout_id: Number(vals.report_layout_id || 0),
        report_tables_id: vals.report_tables_id || "light",
        font: vals.font || "Lato",
        logo: vals.logo || false,
        primary_color: (vals.primary_color || "#212529").toLowerCase(),
        secondary_color: (vals.secondary_color || "#212529").toLowerCase(),
        company_details: vals.company_details || "",
        company_details_placeholder: vals.company_details_placeholder || "",
        report_header: vals.report_header || "",
        report_footer: vals.report_footer || "",
        paperformat_id: Number(vals.paperformat_id || 0),
        vat: vals.vat || "",
        vat_label: vals.vat_label || _t("Tax ID"),
        account_number: vals.account_number || "",
        qr_code: Boolean(vals.qr_code),
    };
}

function guessBase64MimeType(b64) {
    if (!b64 || typeof b64 !== "string") {
        return "image/png";
    }
    if (b64.startsWith("/9j/")) {
        return "image/jpeg";
    }
    if (b64.startsWith("PHN2Z") || b64.startsWith("PD94b")) {
        return "image/svg+xml";
    }
    if (b64.startsWith("UklGR")) {
        return "image/webp";
    }
    if (b64.startsWith("R0lGOD")) {
        return "image/gif";
    }
    return "image/png";
}

export function fetchPreviewPdf({ reportUrl, reportType, context, layoutPreview = null, onXhr = null }) {
    return new Promise((resolve, reject) => {
        const xhr = new browser.XMLHttpRequest();
        xhr.open("POST", "/report/preview_pdf");
        xhr.responseType = "blob";

        const formData = new FormData();
        formData.append("data", JSON.stringify([reportUrl, reportType]));
        formData.append("context", JSON.stringify(context || {}));
        formData.append("token", "dummy-because-api-expects-one");
        if (layoutPreview && typeof layoutPreview === "object") {
            formData.append("layout_preview", JSON.stringify(layoutPreview));
        }
        if (globalThis.odoo?.csrf_token) {
            formData.append("csrf_token", globalThis.odoo.csrf_token);
        }

        xhr.onabort = () => {
            reject(Object.assign(new Error("aborted"), { aborted: true }));
        };

        xhr.onload = () => {
            const mimetype = xhr.response?.type || "";
            const header = (xhr.getResponseHeader("Content-Disposition") || "").replace(/;$/, "");
            let filename = null;
            if (header) {
                try {
                    filename = parseContentDisposition(header).parameters.filename || null;
                } catch {
                    filename = null;
                }
            }
            if (xhr.status === 200 && mimetype === "application/pdf") {
                resolve({ pdfBlob: xhr.response, filename });
            } else if (xhr.status === 502) {
                reject(new ConnectionLostError("/report/preview_pdf"));
            } else {
                const decoder = new FileReader();
                decoder.onload = () => {
                    const contents = decoder.result || "";
                    const doc = new DOMParser().parseFromString(contents, "text/html");
                    const nodes =
                        doc.body.children.length === 0 ? [doc.body] : doc.body.children;
                    let error;
                    try {
                        const node = nodes[1] || nodes[0];
                        error = JSON.parse(node.textContent);
                    } catch {
                        error = {
                            message: "Arbitrary Uncaught Python Exception",
                            data: {
                                debug:
                                    `${xhr.status}\n` +
                                    `${nodes.length > 0 ? nodes[0].textContent : ""}\n` +
                                    `${nodes.length > 1 ? nodes[1].textContent : ""}`,
                            },
                        };
                    }
                    reject(makeErrorFromResponse(error));
                };
                decoder.readAsText(xhr.response);
            }
        };
        xhr.onerror = () => {
            reject(new ConnectionLostError("/report/preview_pdf"));
        };
        onXhr?.(xhr);
        xhr.send(formData);
    });
}

export class PrintPreviewDialog extends Component {
    static template = "custom_invoice_report.PrintPreviewDialog";
    static components = { Dialog, Dropdown, DropdownItem };

    props = useProps({
        close: t.function(),
        title: t.string().optional(),
        pdfBlob: t.any(),
        filename: t.string().optional(),
        reportUrl: t.string(),
        reportType: t.string(),
        reportName: t.string().optional(),
        reportLabel: t.string().optional(),
        actionContext: t.object().optional(),
        actionData: t.any().optional(),
        fallbackModel: t.string().optional(),
        fallbackResId: t.number().optional(),
        editable: t.boolean().optional(false),
        availableReports: t.array(t.any()).optional([]),
        activeIds: t.array(t.number()).optional([]),
        layoutState: t.object().optional({ can_customize: false }),
        onLogged: t.function().optional(),
    });

    iframeRef = signal.ref();
    logoInputRef = signal.ref();
    fontSelectRef = signal.ref();
    paperformatSelectRef = signal.ref();
    primaryColorInputRef = signal.ref();
    secondaryColorInputRef = signal.ref();
    addressInputRef = signal.ref();
    taglineInputRef = signal.ref();
    footerInputRef = signal.ref();
    vatInputRef = signal.ref();
    bankAccountInputRef = signal.ref();
    qrCheckboxRef = signal.ref();

    _bindControlValue(refSignal, getter) {
        useEffect(() => {
            const el = refSignal();
            const val = getter();
            if (el && el.value !== val) {
                el.value = val;
            }
        });
    }

    setup() {
        this.notification = useService("notification");
        const initialReportName = this.props.reportName || "";
        const initialLabel = this.props.reportLabel || this.props.title || _t("Print Preview");
        const initialEditable = Boolean(
            this.props.editable || DEFAULT_EDITABLE_REPORT_NAMES.has(initialReportName)
        );
        const layoutState = this.props.layoutState || { can_customize: false };
        const canCustomize = Boolean(layoutState.can_customize && layoutState.company_id);

        this.currentPdfBlob = this.props.pdfBlob;
        this._previousObjectUrlToRevoke = null;
        this._boundIframeWindow = null;
        this._internalPrintInProgress = false;
        this._internalDownloadInProgress = false;
        this._switchSeq = 0;
        this._renderSeq = 0;
        this._logoExtractSeq = 0;
        this._liveRenderTimer = null;
        this._activePreviewXhr = null;
        this._activePreviewPromise = null;
        this._iframeLoadWaiters = [];
        this._destroyed = false;

        this.state = proxy({
            viewerReady: false,
            switching: false,
            renderingLayout: false,
            savingLayout: false,
            busy: false,
            fullscreen: false,
            sidebarCollapsed: false,
            canCustomize,
            companyId: Number(layoutState.company_id || 0),
            fromInvoice: Boolean(layoutState.from_invoice),
            savedLayout: cloneLayoutValues(layoutState.values || {}),
            draftLayout: cloneLayoutValues(layoutState.values || {}),
            savedLogoPrimaryColor: layoutState.logo_primary_color || false,
            savedLogoSecondaryColor: layoutState.logo_secondary_color || false,
            logoPrimaryColor: layoutState.logo_primary_color || false,
            logoSecondaryColor: layoutState.logo_secondary_color || false,
            pdfHasDirtyPreview: false,
            currentReportUrl: this.props.reportUrl,
            currentReportType: this.props.reportType,
            currentReportName: initialReportName,
            currentReportLabel: initialLabel,
            currentFilename: this.props.filename || `${initialLabel || "report"}.pdf`,
            currentEditable: initialEditable,
            objectUrl: URL.createObjectURL(this.currentPdfBlob),
        });

        this._onWebViewerLoaded = (evt) => {
            const iframeWin = this.iframeRef()?.contentWindow;
            if (!iframeWin || evt.detail?.source !== iframeWin) {
                return;
            }
            const appOptions = iframeWin.PDFViewerApplicationOptions;
            if (!appOptions) {
                return;
            }
            appOptions.setAll({
                disablePreferences: true,
                enableScripting: false,
                externalLinkTarget: 2,
                viewerCssTheme: document.cookie.includes("color_scheme=dark") ? 2 : 1,
                annotationEditorMode: this.state.currentEditable ? 0 : -1,
            });
        };
        document.addEventListener("webviewerloaded", this._onWebViewerLoaded);

        this._bindControlValue(this.fontSelectRef, () => this.state.draftLayout.font || "Lato");
        this._bindControlValue(
            this.paperformatSelectRef,
            () => String(this.state.draftLayout.paperformat_id || "")
        );
        this._bindControlValue(
            this.primaryColorInputRef,
            () => this.state.draftLayout.primary_color || "#212529"
        );
        this._bindControlValue(
            this.secondaryColorInputRef,
            () => this.state.draftLayout.secondary_color || "#212529"
        );
        this._bindControlValue(this.addressInputRef, () => this.state.draftLayout.company_details || "");
        this._bindControlValue(this.taglineInputRef, () => this.state.draftLayout.report_header || "");
        this._bindControlValue(this.footerInputRef, () => this.state.draftLayout.report_footer || "");
        this._bindControlValue(this.vatInputRef, () => this.state.draftLayout.vat || "");
        this._bindControlValue(
            this.bankAccountInputRef,
            () => this.state.draftLayout.account_number || ""
        );
        useEffect(() => {
            const el = this.qrCheckboxRef();
            const checked = Boolean(this.state.draftLayout.qr_code);
            if (el && el.checked !== checked) {
                el.checked = checked;
            }
        });

        onWillDestroy(() => {
            this._destroyed = true;
            if (this._liveRenderTimer) {
                browser.clearTimeout(this._liveRenderTimer);
                this._liveRenderTimer = null;
            }
            this._activePreviewXhr?.abort();
            this._activePreviewXhr = null;
            this._resolveIframeLoadWaiters();
            document.removeEventListener("webviewerloaded", this._onWebViewerLoaded);
            this._clearViewerBeforeUnload();
            if (this._previousObjectUrlToRevoke) {
                URL.revokeObjectURL(this._previousObjectUrlToRevoke);
                this._previousObjectUrlToRevoke = null;
            }
            if (this.state.objectUrl) {
                URL.revokeObjectURL(this.state.objectUrl);
                this.state.objectUrl = "";
            }
        });
    }

    get dialogTitle() {
        return this.state.currentReportLabel || this.props.title || _t("Print Preview");
    }

    get viewerUrl() {
        const encoded = encodeURIComponent(this.state.objectUrl);
        return `/web/static/lib/pdfjs/web/viewer.html?file=${encoded}`;
    }

    get layoutOptions() {
        return this.props.layoutState?.layouts || [];
    }

    get tableStyleOptions() {
        return this.props.layoutState?.table_styles || [];
    }

    get fontOptions() {
        return this.props.layoutState?.fonts || [];
    }

    get paperformatOptions() {
        return this.props.layoutState?.paperformats || [];
    }

    get canToggleSidebar() {
        return Boolean(this.state.canCustomize && this.state.currentEditable);
    }

    get showCustomizer() {
        return Boolean(this.canToggleSidebar && !this.state.sidebarCollapsed);
    }

    get logoDataUrl() {
        const logo = this.state.draftLayout.logo;
        if (!logo) {
            return "";
        }
        return `data:${guessBase64MimeType(logo)};base64,${logo}`;
    }

    get hasCustomColors() {
        const d = this.state.draftLayout;
        const lp = (this.state.logoPrimaryColor || "").toLowerCase();
        const ls = (this.state.logoSecondaryColor || "").toLowerCase();
        return Boolean(
            d.logo &&
                d.primary_color &&
                d.secondary_color &&
                lp &&
                ls &&
                (d.primary_color.toLowerCase() !== lp || d.secondary_color.toLowerCase() !== ls)
        );
    }

    get missingBankForQr() {
        return Boolean(
            this.state.draftLayout.qr_code &&
                !(this.state.draftLayout.account_number || "").trim()
        );
    }

    get isLayoutDirty() {
        if (!this.state.canCustomize) {
            return false;
        }
        const d = this.state.draftLayout;
        const s = this.state.savedLayout;
        return Boolean(
            Number(d.report_layout_id) !== Number(s.report_layout_id) ||
                d.report_tables_id !== s.report_tables_id ||
                d.font !== s.font ||
                (d.logo || false) !== (s.logo || false) ||
                (d.primary_color || "").toLowerCase() !== (s.primary_color || "").toLowerCase() ||
                (d.secondary_color || "").toLowerCase() !== (s.secondary_color || "").toLowerCase() ||
                (d.company_details || "") !== (s.company_details || "") ||
                (d.report_header || "") !== (s.report_header || "") ||
                (d.report_footer || "") !== (s.report_footer || "") ||
                Number(d.paperformat_id) !== Number(s.paperformat_id) ||
                (d.vat || "") !== (s.vat || "") ||
                (d.account_number || "") !== (s.account_number || "") ||
                Boolean(d.qr_code) !== Boolean(s.qr_code)
        );
    }

    _getDirtyChangesMap() {
        const d = this.state.draftLayout;
        const s = this.state.savedLayout;
        const diff = {};
        if (Number(d.report_layout_id) !== Number(s.report_layout_id)) {
            diff.report_layout_id = Number(d.report_layout_id);
        }
        if (d.report_tables_id !== s.report_tables_id) {
            diff.report_tables_id = d.report_tables_id;
        }
        if (d.font !== s.font) {
            diff.font = d.font;
        }
        if ((d.logo || false) !== (s.logo || false)) {
            diff.logo = d.logo || false;
        }
        if ((d.primary_color || "").toLowerCase() !== (s.primary_color || "").toLowerCase()) {
            diff.primary_color = d.primary_color;
        }
        if ((d.secondary_color || "").toLowerCase() !== (s.secondary_color || "").toLowerCase()) {
            diff.secondary_color = d.secondary_color;
        }
        if ((d.company_details || "") !== (s.company_details || "")) {
            diff.company_details = d.company_details || "";
        }
        if ((d.report_header || "") !== (s.report_header || "")) {
            diff.report_header = d.report_header || "";
        }
        if ((d.report_footer || "") !== (s.report_footer || "")) {
            diff.report_footer = d.report_footer || "";
        }
        if (Number(d.paperformat_id) !== Number(s.paperformat_id)) {
            diff.paperformat_id = Number(d.paperformat_id);
        }
        if ((d.vat || "") !== (s.vat || "")) {
            diff.vat = d.vat || "";
        }
        if ((d.account_number || "") !== (s.account_number || "")) {
            diff.account_number = d.account_number || "";
        }
        if (Boolean(d.qr_code) !== Boolean(s.qr_code)) {
            diff.qr_code = Boolean(d.qr_code);
        }
        return diff;
    }

    _getDirtyLayoutPayload() {
        if (!this.state.canCustomize || !this.state.companyId) {
            return null;
        }
        const diff = this._getDirtyChangesMap();
        if (!Object.keys(diff).length) {
            return null;
        }
        return {
            company_id: this.state.companyId,
            ...diff,
        };
    }

    _buildReportContext() {
        return {
            ...(this.props.actionContext || {}),
            active_ids: this.props.activeIds?.length
                ? this.props.activeIds
                : this.props.actionContext?.active_ids,
        };
    }

    toggleFullscreen() {
        this.state.fullscreen = !this.state.fullscreen;
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
    }

    onRequestClose(closeFn) {
        if (this._liveRenderTimer) {
            browser.clearTimeout(this._liveRenderTimer);
            this._liveRenderTimer = null;
        }
        this._activePreviewXhr?.abort();
        this._clearViewerBeforeUnload();
        if (closeFn) {
            closeFn();
        } else {
            this.props.close();
        }
    }

    _clearViewerBeforeUnload() {
        try {
            const app = this.iframeRef()?.contentWindow?.PDFViewerApplication;
            app?.pdfDocument?.annotationStorage?.resetModified?.();
        } catch {
            // Ignore cross-context access edge cases if iframe is tearing down.
        }
    }

    _applyToolbarCss(iframe) {
        try {
            const doc = iframe.contentDocument;
            if (!doc?.head) {
                return;
            }
            let style = doc.getElementById("custom_print_preview_hide_css");
            if (!style) {
                style = doc.createElement("style");
                style.id = "custom_print_preview_hide_css";
                doc.head.appendChild(style);
            }
            style.textContent = buildPdfJsToolbarCss(this.state.currentEditable);
        } catch {
            // Ignore cross-context access edge cases if frame already unloaded.
        }
    }

    _bindIframeEvents(iframe) {
        try {
            const win = iframe.contentWindow;
            if (!win || this._boundIframeWindow === win) {
                return;
            }
            this._boundIframeWindow = win;
            win.addEventListener("beforeprint", () => {
                if (!this._internalPrintInProgress) {
                    void this._logAction("print");
                }
            });
            const app = win.PDFViewerApplication;
            if (app?.initializedPromise) {
                app.initializedPromise
                    .then(() => {
                        if (this._destroyed || this._boundIframeWindow !== win) {
                            return;
                        }
                        app.eventBus?._on("download", () => {
                            if (!this._internalDownloadInProgress) {
                                void this._logAction("download");
                            }
                        });
                    })
                    .catch(() => {});
            }
        } catch {
            // Ignore if iframe window is unavailable.
        }
    }

    _resolveIframeLoadWaiters() {
        while (this._iframeLoadWaiters.length) {
            const resolve = this._iframeLoadWaiters.shift();
            resolve();
        }
    }

    _waitForViewerReady() {
        if (this.state.viewerReady) {
            return Promise.resolve();
        }
        return new Promise((resolve) => {
            this._iframeLoadWaiters.push(resolve);
            browser.setTimeout(() => {
                const idx = this._iframeLoadWaiters.indexOf(resolve);
                if (idx >= 0) {
                    this._iframeLoadWaiters.splice(idx, 1);
                    resolve();
                }
            }, 3000);
        });
    }

    onIframeLoad() {
        if (this._previousObjectUrlToRevoke) {
            URL.revokeObjectURL(this._previousObjectUrlToRevoke);
            this._previousObjectUrlToRevoke = null;
        }
        this.state.viewerReady = true;
        this._resolveIframeLoadWaiters();
        const iframe = this.iframeRef();
        if (!iframe) {
            return;
        }
        this._applyToolbarCss(iframe);
        this._bindIframeEvents(iframe);
    }

    _swapPreviewBlob(pdfBlob, filename, isDirtyPreview) {
        const app = this.iframeRef()?.contentWindow?.PDFViewerApplication;
        const hadAnnotations = Boolean(
            this.state.currentEditable && app?.pdfDocument?.annotationStorage?.size > 0
        );
        if (hadAnnotations) {
            this.notification.add(
                _t("Preview refreshed for the updated layout; temporary PDF markups were cleared."),
                { type: "info" }
            );
        }

        this._clearViewerBeforeUnload();
        this._boundIframeWindow = null;

        const nextObjectUrl = URL.createObjectURL(pdfBlob);
        if (this._previousObjectUrlToRevoke) {
            URL.revokeObjectURL(this._previousObjectUrlToRevoke);
        }
        this._previousObjectUrlToRevoke = this.state.objectUrl;
        this.currentPdfBlob = pdfBlob;

        this.state.viewerReady = false;
        this.state.pdfHasDirtyPreview = Boolean(isDirtyPreview);
        if (filename) {
            this.state.currentFilename = filename;
        }
        this.state.objectUrl = nextObjectUrl;
    }

    _scheduleLivePreview(delayMs = 450) {
        if (!this.state.canCustomize || !this.state.currentEditable) {
            return;
        }
        if (this._liveRenderTimer) {
            browser.clearTimeout(this._liveRenderTimer);
        }
        this._liveRenderTimer = browser.setTimeout(() => {
            this._liveRenderTimer = null;
            void this._refreshLivePreviewPdf();
        }, delayMs);
    }

    async _refreshLivePreviewPdf() {
        if (this._destroyed || !this.state.canCustomize || !this.state.currentEditable) {
            return;
        }
        const dirtyPayload = this._getDirtyLayoutPayload();
        this._activePreviewXhr?.abort();
        this._activePreviewXhr = null;
        if (!dirtyPayload && !this.state.pdfHasDirtyPreview) {
            ++this._renderSeq;
            this.state.renderingLayout = false;
            return;
        }

        const seq = ++this._renderSeq;
        this.state.renderingLayout = true;

        const runPromise = (async () => {
            try {
                const { pdfBlob, filename } = await fetchPreviewPdf({
                    reportUrl: this.state.currentReportUrl,
                    reportType: this.state.currentReportType,
                    context: this._buildReportContext(),
                    layoutPreview: dirtyPayload,
                    onXhr: (xhr) => {
                        this._activePreviewXhr = xhr;
                    },
                });
                if (this._destroyed || seq !== this._renderSeq) {
                    return;
                }
                this._swapPreviewBlob(pdfBlob, filename, Boolean(dirtyPayload));
            } catch (err) {
                if (err?.aborted || this._destroyed || seq !== this._renderSeq) {
                    return;
                }
                this.notification.add(
                    err?.message || _t("Unable to refresh the layout preview."),
                    { type: "danger" }
                );
            } finally {
                if (!this._destroyed && seq === this._renderSeq) {
                    this._activePreviewXhr = null;
                    this.state.renderingLayout = false;
                }
            }
        })();

        this._activePreviewPromise = runPromise;
        await runPromise;
        if (this._activePreviewPromise === runPromise) {
            this._activePreviewPromise = null;
        }
    }

    async _ensurePreviewSynced() {
        if (this._liveRenderTimer) {
            browser.clearTimeout(this._liveRenderTimer);
            this._liveRenderTimer = null;
            await this._refreshLivePreviewPdf();
        } else if (this._activePreviewPromise) {
            await this._activePreviewPromise;
        }
        await this._waitForViewerReady();
    }

    onSelectLayout(layoutId) {
        this.state.draftLayout.report_layout_id = Number(layoutId);
        this._scheduleLivePreview(100);
    }

    onSelectTableStyle(styleVal) {
        this.state.draftLayout.report_tables_id = styleVal;
        this._scheduleLivePreview(100);
    }

    onChangeFont(ev) {
        this.state.draftLayout.font = ev.target.value;
        this._scheduleLivePreview(100);
    }

    onChangePaperformat(ev) {
        this.state.draftLayout.paperformat_id = Number(ev.target.value);
        this._scheduleLivePreview(100);
    }

    onInputColor(field, ev) {
        this.state.draftLayout[field] = (ev.target.value || "#212529").toLowerCase();
        this._scheduleLivePreview(420);
    }

    onChangeColor(field, ev) {
        this.state.draftLayout[field] = (ev.target.value || "#212529").toLowerCase();
        this._scheduleLivePreview(100);
    }

    onResetLogoColors() {
        if (!this.state.logoPrimaryColor && !this.state.logoSecondaryColor) {
            return;
        }
        this.state.draftLayout.primary_color = (
            this.state.logoPrimaryColor || "#000000"
        ).toLowerCase();
        this.state.draftLayout.secondary_color = (
            this.state.logoSecondaryColor || "#000000"
        ).toLowerCase();
        this._scheduleLivePreview(100);
    }

    onTriggerLogoUpload() {
        this.logoInputRef()?.click();
    }

    onLogoFileChange(ev) {
        const file = ev.target?.files?.[0];
        if (!file) {
            return;
        }
        const extractSeq = ++this._logoExtractSeq;
        const reader = new FileReader();
        reader.onload = async () => {
            const result = String(reader.result || "");
            const commaIdx = result.indexOf(",");
            const base64Data = commaIdx >= 0 ? result.slice(commaIdx + 1) : result;
            if (!base64Data || this._destroyed || extractSeq !== this._logoExtractSeq) {
                return;
            }
            this.state.draftLayout.logo = base64Data;
            try {
                const colors = await rpc("/report/extract_logo_colors", { logo: base64Data });
                if (this._destroyed || extractSeq !== this._logoExtractSeq) {
                    return;
                }
                this.state.logoPrimaryColor = colors?.logo_primary_color || false;
                this.state.logoSecondaryColor = colors?.logo_secondary_color || false;
                if (colors?.logo_primary_color) {
                    this.state.draftLayout.primary_color = colors.logo_primary_color.toLowerCase();
                }
                if (colors?.logo_secondary_color) {
                    this.state.draftLayout.secondary_color = colors.logo_secondary_color.toLowerCase();
                }
            } catch {
                // Keep chosen logo even if dominant color extraction fails.
            }
            this._scheduleLivePreview(80);
        };
        reader.readAsDataURL(file);
        ev.target.value = "";
    }

    onClearLogo() {
        ++this._logoExtractSeq;
        this.state.draftLayout.logo = false;
        this.state.logoPrimaryColor = false;
        this.state.logoSecondaryColor = false;
        if (this.logoInputRef()) {
            this.logoInputRef().value = "";
        }
        this._scheduleLivePreview(100);
    }

    onInputField(field, ev) {
        this.state.draftLayout[field] = ev.target.value;
        this._scheduleLivePreview(450);
    }

    onToggleQrCode(ev) {
        this.state.draftLayout.qr_code = Boolean(ev.target.checked);
        this._scheduleLivePreview(100);
    }

    onRevertLayout() {
        if (!this.isLayoutDirty || this.state.busy || this.state.savingLayout) {
            return;
        }
        if (this._liveRenderTimer) {
            browser.clearTimeout(this._liveRenderTimer);
            this._liveRenderTimer = null;
        }
        this.state.draftLayout = cloneLayoutValues(this.state.savedLayout);
        this.state.logoPrimaryColor = this.state.savedLogoPrimaryColor;
        this.state.logoSecondaryColor = this.state.savedLogoSecondaryColor;
        void this._refreshLivePreviewPdf();
    }

    async _saveLayoutChanges() {
        if (!this.state.canCustomize || !this.isLayoutDirty) {
            return true;
        }
        if (this.missingBankForQr) {
            this.notification.add(
                _t("Please enter a Bank Account number before saving QR Code."),
                { type: "warning" }
            );
            this.bankAccountInputRef()?.focus();
            return false;
        }
        const diff = this._getDirtyChangesMap();
        if (!Object.keys(diff).length) {
            return true;
        }

        this.state.savingLayout = true;
        try {
            const updatedState = await rpc("/report/save_document_layout", {
                company_id: this.state.companyId,
                values: diff,
                from_invoice: this.state.fromInvoice,
            });
            if (updatedState?.values) {
                this.state.savedLayout = cloneLayoutValues(updatedState.values);
                this.state.draftLayout = cloneLayoutValues(updatedState.values);
                this.state.savedLogoPrimaryColor = updatedState.logo_primary_color || false;
                this.state.savedLogoSecondaryColor = updatedState.logo_secondary_color || false;
                this.state.logoPrimaryColor = this.state.savedLogoPrimaryColor;
                this.state.logoSecondaryColor = this.state.savedLogoSecondaryColor;
            } else {
                this.state.savedLayout = cloneLayoutValues(this.state.draftLayout);
            }
            this.state.pdfHasDirtyPreview = false;
            return true;
        } catch (err) {
            this.notification.add(
                err?.data?.message || err?.message || _t("Unable to save the document layout."),
                { type: "danger" }
            );
            return false;
        } finally {
            this.state.savingLayout = false;
        }
    }

    async onClickSaveLayout() {
        if (this.state.busy || this.state.savingLayout || !this.isLayoutDirty) {
            return;
        }
        await this._ensurePreviewSynced();
        const saved = await this._saveLayoutChanges();
        if (saved && !this._destroyed) {
            this.notification.add(_t("Company document layout saved."), { type: "success" });
        }
    }

    async onSelectReport(report) {
        if (!report || this.state.busy || report.report_name === this.state.currentReportName) {
            return;
        }
        if (this._liveRenderTimer) {
            browser.clearTimeout(this._liveRenderTimer);
            this._liveRenderTimer = null;
        }
        this._activePreviewXhr?.abort();

        const seq = ++this._switchSeq;
        this.state.switching = true;
        try {
            const switchContext = this._buildReportContext();
            const nextReportUrl = getReportUrl(
                {
                    report_name: report.report_name,
                    report_type: report.report_type || "qweb-pdf",
                    data: this.props.actionData || null,
                    context: switchContext,
                },
                "pdf",
                switchContext
            );
            const nextEditable = Boolean(
                report.editable ?? DEFAULT_EDITABLE_REPORT_NAMES.has(report.report_name)
            );
            const dirtyPayload = nextEditable ? this._getDirtyLayoutPayload() : null;

            const { pdfBlob, filename } = await fetchPreviewPdf({
                reportUrl: nextReportUrl,
                reportType: report.report_type || "qweb-pdf",
                context: switchContext,
                layoutPreview: dirtyPayload,
                onXhr: (xhr) => {
                    this._activePreviewXhr = xhr;
                },
            });
            if (this._destroyed || seq !== this._switchSeq) {
                return;
            }

            this.state.currentReportUrl = nextReportUrl;
            this.state.currentReportType = report.report_type || "qweb-pdf";
            this.state.currentReportName = report.report_name;
            this.state.currentReportLabel = report.name || report.report_name;
            this.state.currentEditable = nextEditable;
            this._swapPreviewBlob(
                pdfBlob,
                filename || `${report.name || report.report_name || "report"}.pdf`,
                Boolean(dirtyPayload)
            );
        } catch (err) {
            if (!err?.aborted && !this._destroyed && seq === this._switchSeq) {
                this.notification.add(
                    err?.message || _t("Unable to load the selected report preview."),
                    { type: "danger" }
                );
            }
        } finally {
            if (!this._destroyed && seq === this._switchSeq) {
                this._activePreviewXhr = null;
                this.state.switching = false;
            }
        }
    }

    async _commitAnnotationEditors(app) {
        if (!this.state.currentEditable || !app?.pdfViewer) {
            return;
        }
        try {
            // Switch out of editor mode FIRST so PDF.js AnnotationEditorUIManager.unselectAll()
            // commits the active editor before blurring clears #activeEditor.
            const mode = app.pdfViewer.annotationEditorMode;
            if (mode && mode > 0) {
                await new Promise((resolve) => {
                    let settled = false;
                    const finish = () => {
                        if (settled) {
                            return;
                        }
                        settled = true;
                        app.eventBus?._off("annotationeditormodechanged", finish);
                        resolve();
                    };
                    app.eventBus?._on("annotationeditormodechanged", finish);
                    app.eventBus?.dispatch("switchannotationeditormode", {
                        source: app,
                        mode: 0,
                    });
                    setTimeout(finish, 180);
                });
            }
            const iframeDoc = this.iframeRef()?.contentDocument;
            if (iframeDoc?.activeElement && typeof iframeDoc.activeElement.blur === "function") {
                iframeDoc.activeElement.blur();
            }
        } catch {
            // Ignore commit edge cases and continue with current annotationStorage.
        }
    }

    async _logAction(actionType) {
        try {
            const res = await rpc("/report/log_print_action", {
                report_url: this.state.currentReportUrl,
                report_type: this.state.currentReportType,
                report_name: this.state.currentReportName,
                report_label: this.dialogTitle,
                action_type: actionType,
                context: this.props.actionContext || {},
                fallback_model: this.props.fallbackModel || "",
                fallback_res_id: this.props.fallbackResId || 0,
            });
            this.props.onLogged?.(res?.targets || []);
        } catch {
            // Keep printing/downloading usable even if audit logging encounters a transient error.
        }
    }

    async onClickPrint() {
        if (this.state.busy || this.state.switching) {
            return;
        }
        if (this.canToggleSidebar && this.isLayoutDirty && this.missingBankForQr) {
            this.notification.add(
                _t("Please enter a Bank Account number before saving QR Code."),
                { type: "warning" }
            );
            this.bankAccountInputRef()?.focus();
            return;
        }

        this.state.busy = true;
        this._internalPrintInProgress = true;
        try {
            if (
                this.canToggleSidebar &&
                (this.isLayoutDirty ||
                    this.state.pdfHasDirtyPreview ||
                    this._liveRenderTimer ||
                    this._activePreviewPromise)
            ) {
                await this._ensurePreviewSynced();
            }
            const iframe = this.iframeRef();
            const win = iframe?.contentWindow;
            if (!win) {
                this.notification.add(_t("PDF preview is not ready yet."), { type: "warning" });
                return;
            }

            const app = win.PDFViewerApplication;
            if (app) {
                await app.initializedPromise;
                await app.pdfViewer?.pagesPromise;
                await this._commitAnnotationEditors(app);
            }

            if (this.canToggleSidebar && this.isLayoutDirty) {
                const ok = await this._saveLayoutChanges();
                if (!ok) {
                    return;
                }
            }

            await this._logAction("print");
            if (app?.triggerPrinting) {
                await app.triggerPrinting();
            } else {
                win.focus();
                win.print();
            }
        } finally {
            this._internalPrintInProgress = false;
            this.state.busy = false;
        }
    }

    async onClickDownload() {
        if (this.state.busy || this.state.switching) {
            return;
        }
        if (this.canToggleSidebar && this.isLayoutDirty && this.missingBankForQr) {
            this.notification.add(
                _t("Please enter a Bank Account number before saving QR Code."),
                { type: "warning" }
            );
            this.bankAccountInputRef()?.focus();
            return;
        }

        this.state.busy = true;
        this._internalDownloadInProgress = true;
        try {
            if (
                this.canToggleSidebar &&
                (this.isLayoutDirty ||
                    this.state.pdfHasDirtyPreview ||
                    this._liveRenderTimer ||
                    this._activePreviewPromise)
            ) {
                await this._ensurePreviewSynced();
            }

            const filename = this.state.currentFilename || "report.pdf";
            let blobToDownload = this.currentPdfBlob;

            const iframe = this.iframeRef();
            const app = iframe?.contentWindow?.PDFViewerApplication;
            if (this.state.currentEditable && app) {
                await app.initializedPromise;
                await app.pdfViewer?.pagesPromise;
                await this._commitAnnotationEditors(app);
                if (app.pdfDocument?.annotationStorage?.size > 0) {
                    const savedBytes = await app.pdfDocument.saveDocument();
                    blobToDownload = new Blob([savedBytes], { type: "application/pdf" });
                }
            }

            if (this.canToggleSidebar && this.isLayoutDirty) {
                const ok = await this._saveLayoutChanges();
                if (!ok) {
                    return;
                }
            }

            downloadFile(blobToDownload, filename, "application/pdf");
            await this._logAction("download");
        } finally {
            this._internalDownloadInProgress = false;
            this.state.busy = false;
        }
    }
}
