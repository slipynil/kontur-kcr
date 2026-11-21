"""Remove client blocking from the registry and recovery data."""

from alembic import op

revision = "0007_remove_client_blocking"
down_revision = "0006_registry_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SELECT pg_advisory_xact_lock(4267590301);

        ALTER TABLE clients DROP COLUMN is_blocked;

        UPDATE clients
        SET field_sources = field_sources - 'is_blocked',
            inherited_fields = inherited_fields - 'is_blocked',
            needs_review = CASE WHEN inherited_fields ? 'is_blocked'
                THEN (inherited_fields - 'is_blocked') <> '{}'::jsonb ELSE needs_review END
        WHERE field_sources ? 'is_blocked' OR inherited_fields ? 'is_blocked';

        UPDATE quarter_snapshot_history
        SET inherited_fields = inherited_fields - 'is_blocked'
        WHERE inherited_fields ? 'is_blocked';

        UPDATE clients_deleted_history
        SET payload = payload #- '{inherited_fields,is_blocked}'
        WHERE payload->'inherited_fields' ? 'is_blocked';

        UPDATE clients_import_undo
        SET previous_state = jsonb_set(previous_state, '{clients}', (
            SELECT jsonb_agg(
                (profile - 'is_blocked' #- '{field_sources,is_blocked}'
                    #- '{inherited_fields,is_blocked}')
                || CASE WHEN profile->'inherited_fields' ? 'is_blocked'
                    THEN jsonb_build_object('needs_review',
                        ((profile->'inherited_fields') - 'is_blocked') <> '{}'::jsonb)
                    ELSE '{}'::jsonb END
                ORDER BY position)
            FROM jsonb_array_elements(previous_state->'clients')
                WITH ORDINALITY AS saved(profile, position)
        ))
        WHERE EXISTS (
            SELECT 1 FROM jsonb_array_elements(previous_state->'clients') AS saved(profile)
            WHERE profile ? 'is_blocked'
                OR profile->'field_sources' ? 'is_blocked'
                OR profile->'inherited_fields' ? 'is_blocked'
        );
    """)


def downgrade() -> None:
    # The previous column can be recreated, but the removed values are lost.
    op.execute("ALTER TABLE clients ADD COLUMN is_blocked BOOLEAN NOT NULL DEFAULT FALSE")
