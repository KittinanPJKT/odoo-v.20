import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Transfer XML IDs and module ownership from merged thai_billing_reports into custom_invoice_report.

    Preserves all existing database columns and user data (branch_name, project_name,
    period, company/partner/invoice/payment fields) without triggering module uninstall.
    """
    cr.execute("SELECT id FROM ir_module_module WHERE name = 'thai_billing_reports'")
    legacy_row = cr.fetchone()
    if not legacy_row:
        return
    legacy_mod_id = legacy_row[0]

    cr.execute("SELECT id FROM ir_module_module WHERE name = 'custom_invoice_report'")
    target_row = cr.fetchone()
    target_mod_id = target_row[0] if target_row else None

    _logger.info("Merging legacy module thai_billing_reports (id=%s) into custom_invoice_report...", legacy_mod_id)

    # 1. Drop legacy XML IDs whose (module, name) already exist in custom_invoice_report
    #    (e.g. shared base reflection entries such as model_account_move, field_*__id, display_name).
    cr.execute("""
        DELETE FROM ir_model_data AS old
        USING ir_model_data AS cur
        WHERE old.module = 'thai_billing_reports'
          AND cur.module = 'custom_invoice_report'
          AND old.name = cur.name
    """)

    # 2. Re-assign remaining XML IDs (custom fields, selections, views) in place so existing IDs stay intact.
    cr.execute("""
        UPDATE ir_model_data
        SET module = 'custom_invoice_report'
        WHERE module = 'thai_billing_reports'
    """)

    # 3. Transfer or clear auxiliary FK tables referencing ir_module_module(id).
    if target_mod_id:
        for tbl in ('ir_model_constraint', 'ir_model_relation'):
            cr.execute("""
                DELETE FROM {tbl} AS old
                USING {tbl} AS cur
                WHERE old.module = %s
                  AND cur.module = %s
                  AND old.name = cur.name
            """.format(tbl=tbl), (legacy_mod_id, target_mod_id))
            cr.execute(
                f"UPDATE {tbl} SET module = %s WHERE module = %s",
                (target_mod_id, legacy_mod_id),
            )

    cr.execute(
        "DELETE FROM ir_module_module_dependency WHERE name = 'thai_billing_reports' OR module_id = %s",
        (legacy_mod_id,),
    )
    for tbl, col in (
        ('module_country', 'module_id'),
        ('ir_module_module_exclusion', 'module_id'),
        ('ir_demo_failure', 'module_id'),
        ('base_module_uninstall_ir_module_module_rel', 'ir_module_module_id'),
        ('rel_modules_langexport', 'module_id'),
        ('base_module_install_request', 'module_id'),
        ('base_module_install_review', 'module_id'),
    ):
        cr.execute("SELECT to_regclass(%s)", (f'public.{tbl}',))
        if cr.fetchone()[0]:
            cr.execute(f"DELETE FROM {tbl} WHERE {col} = %s", (legacy_mod_id,))

    cr.execute("SELECT to_regclass('public.payment_provider')")
    if cr.fetchone()[0]:
        cr.execute("UPDATE payment_provider SET module_id = NULL WHERE module_id = %s", (legacy_mod_id,))

    # 4. Remove obsolete ir_module_module row (module folder is removed from custom_addons).
    cr.execute("DELETE FROM ir_module_module WHERE id = %s", (legacy_mod_id,))

