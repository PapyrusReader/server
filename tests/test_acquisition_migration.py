"""Managed acquisition migration contract tests."""

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def test_managed_acquisition_has_a_follow_up_migration() -> None:
    revisions = [
        path
        for path in MIGRATIONS.glob("*.py")
        if path.name != "d4e5f6a7b8c9_add_acquisition.py"
        and 'down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"' in path.read_text(encoding="utf-8")
    ]

    assert len(revisions) == 1

    migration = revisions[0].read_text(encoding="utf-8")
    for column in (
        "download_root",
        "book_id",
        "client_hash",
        "client_state",
        "progress_basis_points",
        "downloaded_bytes",
        "total_bytes",
        "download_speed_bytes_per_second",
        "eta_seconds",
        "selected_file_path",
        "retry_count",
        "next_poll_at",
        "lease_owner",
        "lease_until",
        "submitted_at",
        "started_at",
        "updated_at",
        "completed_at",
        "cancelled_at",
    ):
        assert f'"{column}"' in migration

    assert '"ix_acquisition_jobs_owner_status"' in migration
    assert '"ix_acquisition_jobs_next_poll_at"' in migration
    assert "def downgrade() -> None:" in migration
