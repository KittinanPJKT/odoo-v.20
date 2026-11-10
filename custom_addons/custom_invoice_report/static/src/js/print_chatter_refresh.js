import { browser } from "@web/core/browser/browser";
import { download } from "@web/core/network/download";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { ActionPlugin } from "@web/webclient/actions/action_plugin";
import { getReportUrl } from "@web/webclient/actions/reports/utils";
import {
    DEFAULT_EDITABLE_REPORT_NAMES,
    PrintPreviewDialog,
    fetchPreviewPdf,
} from "./print_preview_dialog";

let reloadThreadCallback = null;
const pdfEngineStatusCache = new Map();

function extractTargetsFromDownload(options, actionService) {
    const targets = [];
    const seen = new Set();
    const pushTarget = (model, id) => {
        const numId = Number(id);
        if (!model || !numId || Number.isNaN(numId)) {
            return;
        }
        const key = `${model}:${numId}`;
        if (!seen.has(key)) {
            seen.add(key);
            targets.push({ model, id: numId });
        }
    };

    try {
        const rawCtx = options?.data?.context;
        if (rawCtx) {
            const ctx = typeof rawCtx === "string" ? JSON.parse(rawCtx) : rawCtx;
            const activeModel = ctx?.active_model;
            if (activeModel) {
                const ids = Array.isArray(ctx.active_ids)
                    ? ctx.active_ids
                    : ctx.active_id
                      ? [ctx.active_id]
                      : [];
                for (const id of ids) {
                    pushTarget(activeModel, id);
                }
            }
        }
    } catch {
        // Ignore malformed context JSON and rely on current controller fallback.
    }

    const currentProps = actionService?.currentController?.props;
    if (currentProps?.resModel && currentProps?.resId) {
        pushTarget(currentProps.resModel, currentProps.resId);
    }
    return targets;
}

async function getPdfEngineStatus(reportType) {
    let engineName = "";
    const typeSuffix = reportType.slice(5);
    if (typeSuffix.startsWith("pdf-")) {
        engineName = typeSuffix.slice(4);
    }
    if (!pdfEngineStatusCache.has(engineName)) {
        const params = engineName ? { engine_name: engineName } : {};
        pdfEngineStatusCache.set(engineName, rpc("/report/get_pdf_engine_state", params));
    }
    try {
        return await pdfEngineStatusCache.get(engineName);
    } catch (err) {
        pdfEngineStatusCache.delete(engineName);
        throw err;
    }
}

async function pdfPreviewReportHandler(action, _options, { services }) {
    if (!action || action.bypass_preview || !action.report_type?.startsWith("qweb-pdf")) {
        return false;
    }
    if (action.context?.jobs?.length) {
        // Leave direct IoT/hardware receipt printer actions to the dedicated printer handler.
        return false;
    }

    const engineStatus = await getPdfEngineStatus(action.report_type);
    if (!["upgrade", "ok"].includes(engineStatus)) {
        // Fall back cleanly to standard Odoo handling (warning + HTML preview).
        return false;
    }

    const actionContext = { ...(action.context || {}) };
    if (!actionContext.active_ids?.length && actionContext.active_id) {
        actionContext.active_ids = [actionContext.active_id];
    }

    const { ui, dialog, action: actionService } = services;
    const currentProps = actionService?.currentController?.props;
    const fallbackModel = currentProps?.resModel || "";
    const fallbackResId = Number(currentProps?.resId) || 0;
    const targetModel = actionContext.active_model || action.model || fallbackModel;
    const hasData = action.data && JSON.stringify(action.data) !== "{}";

    actionContext.skip_layout_configurator = true;
    actionContext.discard_logo_check = true;

    const normalizedAction = { ...action, context: actionContext };
    const reportUrl = getReportUrl(normalizedAction, "pdf");
    const previewContext = { ...user.context, ...actionContext };

    const rawResIds = actionContext.active_ids?.length
        ? actionContext.active_ids
        : fallbackResId && (!targetModel || targetModel === fallbackModel)
          ? [fallbackResId]
          : [];
    const activeIds = rawResIds
        .map((id) => Number(id))
        .filter((id) => !Number.isNaN(id) && id > 0);

    let pdfBlob;
    let filename;
    let availableReports = [];
    let layoutState = { can_customize: false };
    ui.block();
    try {
        const isInvoicePreview = !hasData && targetModel === "account.move" && activeIds.length > 0;
        const availableReportsPromise = isInvoicePreview
            ? rpc("/report/preview_available_reports", {
                  model: targetModel,
                  res_ids: activeIds,
                  current_report_name: action.report_name || "",
              }).catch(() => [])
            : Promise.resolve([]);
        const layoutStatePromise = isInvoicePreview
            ? rpc("/report/preview_layout_state", {
                  model: targetModel,
                  res_ids: activeIds,
              }).catch(() => ({ can_customize: false }))
            : Promise.resolve({ can_customize: false });

        [{ pdfBlob, filename }, availableReports, layoutState] = await Promise.all([
            fetchPreviewPdf({
                reportUrl,
                reportType: action.report_type,
                context: previewContext,
            }),
            availableReportsPromise,
            layoutStatePromise,
        ]);
    } finally {
        ui.unblock();
    }

    const matchedReport = availableReports.find((r) => r.report_name === action.report_name);
    const initialEditable = Boolean(
        matchedReport?.editable ?? DEFAULT_EDITABLE_REPORT_NAMES.has(action.report_name)
    );
    const reportLabel = action.name || action.display_name || action.report_name || "";

    const openPreviewModal = () => {
        dialog.add(PrintPreviewDialog, {
            title: action.display_name || action.name || filename || action.report_name,
            pdfBlob,
            filename: filename || `${reportLabel || "report"}.pdf`,
            reportUrl,
            reportType: action.report_type,
            reportName: action.report_name || "",
            reportLabel,
            actionContext: previewContext,
            actionData: action.data || null,
            fallbackModel,
            fallbackResId,
            editable: initialEditable,
            availableReports,
            activeIds,
            layoutState,
            onLogged: (targets) => {
                if (targets?.length) {
                    for (const target of targets) {
                        reloadThreadCallback?.(target.model, target.id);
                    }
                } else if (fallbackModel && fallbackResId) {
                    reloadThreadCallback?.(fallbackModel, fallbackResId);
                }
            },
        });
    };

    if (action.close_on_report_download) {
        // Let ActionPlugin finish closing the wizard dialog before opening the preview modal.
        browser.setTimeout(openPreviewModal, 0);
    } else {
        openPreviewModal();
    }
    return true;
}

registry
    .category("ir.actions.report handlers")
    .add("custom_invoice_report.pdf_preview_handler", pdfPreviewReportHandler, { sequence: 100 });

patch(ActionPlugin.prototype, {
    setup() {
        super.setup(...arguments);
        const origDoActionButton = this.doActionButton;
        this.doActionButton = async (params, opts) => {
            const fromSendWizard = params?.resModel === "account.move.send.wizard";
            const result = await origDoActionButton(params, opts);
            if (fromSendWizard && reloadThreadCallback) {
                const currentProps = this.currentController?.props;
                if (currentProps?.resModel && currentProps?.resId) {
                    browser.setTimeout(() => {
                        reloadThreadCallback?.(currentProps.resModel, currentProps.resId);
                    }, 900);
                }
            }
            return result;
        };
    },
});

export const printAuditLogSyncService = {
    dependencies: ["action", "bus_service", "mail.store"],
    start(env, { action, bus_service, "mail.store": mailStore }) {
        const lastReloadAt = new Map();

        const reloadThread = (model, id) => {
            const numId = Number(id);
            if (!model || !numId || Number.isNaN(numId)) {
                return;
            }
            const key = `${model}:${numId}`;
            const now = Date.now();
            const prev = lastReloadAt.get(key) || 0;
            if (now - prev < 150) {
                return;
            }
            lastReloadAt.set(key, now);

            env.bus.trigger("MAIL:RELOAD-THREAD", { model, id: numId });
            const thread = mailStore["mail.thread"].get({ model, id: numId });
            thread?.fetchNewMessages?.();
        };

        reloadThreadCallback = reloadThread;

        if (!download._customPrintAuditPatched) {
            const origDownload = download._download;
            download._customPrintAuditPatched = true;
            download._download = async (options) => {
                const result = await origDownload(options);
                if (options?.url === "/report/download") {
                    const targets = extractTargetsFromDownload(options, action);
                    for (const target of targets) {
                        reloadThread(target.model, target.id);
                    }
                }
                return result;
            };
        }

        bus_service.subscribe("custom_invoice_report/print_logged", (payload) => {
            for (const rec of payload?.records || []) {
                if (rec?.model && rec?.id) {
                    reloadThread(rec.model, rec.id);
                }
            }
        });

        return { reloadThread };
    },
};

registry.category("services").add("custom_invoice_report.print_audit_log_sync", printAuditLogSyncService);
