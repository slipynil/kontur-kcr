"""Remove the old observation quarter and date from registry field sources."""

from alembic import op

revision = "0006_registry_provenance"
down_revision = "0005_client_blocking_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SELECT pg_advisory_xact_lock(4267590301);

        -- Every accepted snapshot row creates a history record, including historical rows.
        CREATE TEMP TABLE registry_source_batches ON COMMIT DROP AS
        SELECT batch_id::text AS id FROM clients_import_operations
        WHERE (result->>'accepted_rows')::integer > 0 AND result->>'created_history_records' = '0';
        CREATE UNIQUE INDEX ON registry_source_batches (id);
        ANALYZE registry_source_batches;

        CREATE FUNCTION pg_temp.clean_registry_sources(sources jsonb) RETURNS jsonb
        LANGUAGE sql STABLE AS $$
            SELECT coalesce(jsonb_object_agg(field.key, CASE WHEN batch.id IS NOT NULL
                THEN field.value - 'quarter' - 'snapshot_date' ELSE field.value END), '{}'::jsonb)
            FROM jsonb_each(sources) AS field
            LEFT JOIN registry_source_batches batch ON batch.id = field.value->>'batch_id'
        $$;

        UPDATE clients
        SET field_sources = pg_temp.clean_registry_sources(field_sources),
            inherited_fields = pg_temp.clean_registry_sources(inherited_fields)
        WHERE field_sources <> '{}'::jsonb OR inherited_fields <> '{}'::jsonb;

        UPDATE clients_import_undo
        SET previous_state = jsonb_set(previous_state, '{clients,0}', previous_state->'clients'->0
            || jsonb_build_object(
                'field_sources',
                pg_temp.clean_registry_sources(previous_state->'clients'->0->'field_sources'),
                'inherited_fields',
                pg_temp.clean_registry_sources(previous_state->'clients'->0->'inherited_fields')
            ))
        WHERE jsonb_array_length(previous_state->'clients') = 1;

        DROP FUNCTION pg_temp.clean_registry_sources(jsonb);
    """)


def downgrade() -> None:
    # Removed registry dates and quarters cannot be reconstructed.
    pass
