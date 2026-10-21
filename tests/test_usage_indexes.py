"""Usage-ledger index and query-shape tests (#436).

The ledger is append-only and grows monotonically, so period aggregates must
stay indexed by owner/workspace and time. These tests pin the composite indexes
and document the expected SQLite query plans for the common shapes.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from app.extensions import db
from app.models import UsageRecord, Workspace

EXPECTED_INDEXES = {
    "ix_usage_records_user_created_at",
    "ix_usage_records_workspace_created_at",
    "ix_usage_records_user_source_created_at",
    "ix_usage_records_workspace_source_created_at",
}


def _seed(user):
    workspace = Workspace(user_id=user.id, name="Indexed workspace")
    db.session.add(workspace)
    db.session.commit()

    now = datetime.now(UTC)
    rows = [
        UsageRecord(
            user_id=user.id,
            workspace_id=workspace.id,
            source="chat" if index % 2 == 0 else "analysis",
            total_tokens=10,
            cost_estimate=0.01,
            status="success",
            created_at=now - timedelta(days=index % 60),
        )
        for index in range(2000)
    ]
    db.session.bulk_save_objects(rows)
    db.session.commit()
    return workspace, now


def _plan(sql, params):
    rows = db.session.execute(text(f"EXPLAIN QUERY PLAN {sql}"), params).fetchall()
    return " ".join(str(row) for row in rows)


class TestUsageLedgerIndexes:
    def test_composite_indexes_are_defined(self):
        names = {index.name for index in UsageRecord.__table__.indexes}
        assert names >= EXPECTED_INDEXES

    def test_user_period_aggregate_uses_an_index(self, db, make_user):
        user = make_user()
        _, now = _seed(user)

        plan = _plan(
            "SELECT SUM(total_tokens) FROM usage_records "
            "WHERE user_id = :user_id AND created_at >= :start AND created_at < :end",
            {
                "user_id": user.id,
                "start": (now - timedelta(days=7)).isoformat(sep=" "),
                "end": (now + timedelta(seconds=1)).isoformat(sep=" "),
            },
        )

        assert "ix_usage_records_user_created_at" in plan or "USING INDEX" in plan
        assert "SCAN usage_records" not in plan

    def test_workspace_period_aggregate_uses_an_index(self, db, make_user):
        user = make_user()
        workspace, now = _seed(user)

        plan = _plan(
            "SELECT SUM(cost_estimate) FROM usage_records "
            "WHERE workspace_id = :workspace_id AND created_at >= :start",
            {
                "workspace_id": workspace.id,
                "start": (now - timedelta(days=30)).isoformat(sep=" "),
            },
        )

        assert "ix_usage_records_workspace_created_at" in plan or "USING INDEX" in plan
        assert "SCAN usage_records" not in plan

    def test_source_rollup_uses_a_composite_index(self, db, make_user):
        user = make_user()
        _, now = _seed(user)

        plan = _plan(
            "SELECT source, SUM(total_tokens) FROM usage_records "
            "WHERE user_id = :user_id AND created_at >= :start GROUP BY source",
            {"user_id": user.id, "start": (now - timedelta(days=30)).isoformat(sep=" ")},
        )

        assert "ix_usage_records_user_source_created_at" in plan or "USING INDEX" in plan
        assert "SCAN usage_records" not in plan
