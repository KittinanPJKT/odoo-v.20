import logging

_logger = logging.getLogger(__name__)

OBSOLETE_MOVE_FIELDS = (
    'payment_method_type',
    'cheque_no',
    'cheque_date',
    'bank_branch',
)


def migrate(cr, version):
    """Remove obsolete receipt/cheque fields once declared on account.move."""
    cr.execute(
        """
        SELECT id, name
        FROM ir_model_fields
        WHERE model = 'account.move'
          AND name = ANY(%s)
        """,
        (list(OBSOLETE_MOVE_FIELDS),),
    )
    rows = cr.fetchall()
    if rows:
        field_ids = [r[0] for r in rows]
        field_names = [r[1] for r in rows]
        _logger.info("Removing obsolete account.move fields metadata: %s", field_names)
        cr.execute(
            """
            DELETE FROM ir_model_data
            WHERE model = 'ir.model.fields'
              AND res_id = ANY(%s)
            """,
            (field_ids,),
        )
        cr.execute("DELETE FROM ir_model_fields WHERE id = ANY(%s)", (field_ids,))

    for col in OBSOLETE_MOVE_FIELDS:
        cr.execute(f'ALTER TABLE account_move DROP COLUMN IF EXISTS "{col}"')

