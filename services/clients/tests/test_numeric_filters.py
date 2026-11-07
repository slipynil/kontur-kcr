from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient

from clients_service.config import Settings
from clients_service.import_application import apply_import
from clients_service.import_models import ImportEnvelope
from clients_service.main import create_app
from clients_service.read_repository import get_card, list_clients

pytestmark = pytest.mark.postgres


@pytest.fixture
def numeric_data(database):
    engine, config, url = database
    command.upgrade(config, "head")

    def apply(kind, quarter, items):
        result = apply_import(
            engine,
            ImportEnvelope(
                batch_id=uuid4(),
                uploaded_by=uuid4(),
                selected_quarter=quarter,
                import_type=kind,
                snapshot_date="2026-08-01",
                items=items,
            ),
        )
        assert result["rejected_rows"] == 0

    apply(
        "REGISTRY",
        "2026_Q3",
        [{"client": {"kcr_code": code}} for code in ["a", "b", "c", "d", "e", "f", "g"]],
    )

    def rows(quarter, counts):
        return [
            {
                "client": {"kcr_code": code},
                "snapshot": {
                    "quarter": quarter,
                    "total_documents": total,
                    "unsubmitted_documents": remaining,
                },
            }
            for code, total, remaining in counts
        ]

    apply("SNAPSHOT", "2026_Q2", rows("2026_Q2", [("a", 100, 40), ("b", 200, 100), ("c", 100, 80)]))
    apply(
        "SNAPSHOT",
        "2026_Q3",
        rows(
            "2026_Q3",
            [
                ("a", 100, 10),
                ("b", 200, 6),
                ("d", 0, 0),
                ("e", 3, 1),
                ("f", 10, 0),
                ("g", 100, 200),
            ],
        ),
    )
    return engine, url


def test_ranges_use_selected_quarter_not_all_periods(numeric_data):
    engine, _ = numeric_data
    result = list_clients(
        engine,
        quarter="2026_Q3",
        min_unsubmitted_documents=10,
        max_unsubmitted_documents=10,
        min_unsubmitted_percent=5,
    )
    assert result.total == 1 and result.items[0].kcr_code == "a"
    assert result.items[0].outstanding_documents == 50
    assert result.items[0].quarter_summary.model_dump() == {
        "quarter": "2026_Q3",
        "total_documents": 100,
        "unsubmitted_documents": 10,
        "unsubmitted_percent": 10,
    }


def test_missing_period_and_zero_denominator_are_not_fake_zero_percent(numeric_data):
    engine, _ = numeric_data
    counts = list_clients(engine, quarter="2026_Q3", min_unsubmitted_documents=0)
    assert counts.total == 6 and "c" not in [row.kcr_code for row in counts.items]
    percentages = list_clients(engine, quarter="2026_Q3", max_unsubmitted_percent=0)
    assert [row.kcr_code for row in percentages.items] == ["f"]
    assert get_card(engine, "c", quarter="2026_Q3").quarter_summary is None
    assert get_card(engine, "d", quarter="2026_Q3").quarter_summary.unsubmitted_percent is None


def test_fractional_percent_is_filtered_without_rounding(numeric_data):
    engine, _ = numeric_data
    selected = list_clients(
        engine,
        quarter="2026_Q3",
        min_unsubmitted_percent=Decimal("33.3333"),
        max_unsubmitted_percent=Decimal("33.3334"),
    )
    assert selected.total == 1 and selected.items[0].kcr_code == "e"
    excluded = list_clients(
        engine,
        quarter="2026_Q3",
        min_unsubmitted_percent=Decimal("33.3334"),
        max_unsubmitted_percent=100,
    )
    assert excluded.total == 0
    # Inconsistent input counts are not silently capped at 100%.
    assert get_card(engine, "g", quarter="2026_Q3").quarter_summary.unsubmitted_percent == 200


def test_numeric_filters_precede_total_and_pagination(numeric_data):
    engine, _ = numeric_data
    selected = list_clients(
        engine, quarter="2026_Q3", min_unsubmitted_percent=5, page_size=1, page=2
    )
    assert selected.total == 3 and selected.items[0].kcr_code == "e"
    empty_page = list_clients(
        engine, quarter="2026_Q3", min_unsubmitted_percent=5, page_size=1, page=4
    )
    assert empty_page.total == 3 and empty_page.items == []


def test_numeric_http_validation(numeric_data):
    _, url = numeric_data
    with TestClient(create_app(Settings(database_url=url, enable_registry_read=True))) as client:
        path = "/internal/v1/clients"
        result = client.get(
            path,
            params={
                "quarter": "2026_Q3",
                "min_unsubmitted_documents": 10,
                "max_unsubmitted_documents": 10,
            },
        )
        assert result.status_code == 200 and result.json()["total"] == 1
        for query in (
            {"min_unsubmitted_documents": -1},
            {"min_unsubmitted_documents": "1.5"},
            {"min_unsubmitted_documents": 10, "max_unsubmitted_documents": 5},
            {"min_unsubmitted_percent": 101},
            {"min_unsubmitted_percent": 5, "max_unsubmitted_percent": 4},
            {"min_unsubmitted_percent": "NaN"},
        ):
            assert client.get(path, params=query).status_code == 400
