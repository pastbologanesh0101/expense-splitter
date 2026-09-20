"""Flask-test-client-level tests: exercise the app factory, routes, form
validation, and the database layer end to end (using an in-memory SQLite
database via ``create_app(testing=True)``).
"""
import pytest

from app import create_app
from app.db import get_db
from app.settlement import compute_net_balances


@pytest.fixture
def app():
    return create_app(testing=True)


@pytest.fixture
def client(app):
    return app.test_client()


def create_group_with_members(client, name, member_names):
    resp = client.post(
        "/groups",
        data={"name": name, "members": ",".join(member_names)},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    return resp


def get_group_and_member_ids(app, group_name):
    with app.app_context():
        db = get_db()
        group = db.execute("SELECT * FROM groups WHERE name = ?", (group_name,)).fetchone()
        members = db.execute(
            "SELECT * FROM members WHERE group_id = ? ORDER BY id", (group["id"],)
        ).fetchall()
        return group["id"], {m["name"]: m["id"] for m in members}


def test_create_group_and_members(client, app):
    create_group_with_members(client, "Trip", ["Alice", "Bob", "Cara"])
    _, ids = get_group_and_member_ids(app, "Trip")
    assert set(ids.keys()) == {"Alice", "Bob", "Cara"}


def test_add_expense_equal_split_creates_correct_shares(client, app):
    create_group_with_members(client, "Roomies", ["Alice", "Bob", "Cara"])
    group_id, ids = get_group_and_member_ids(app, "Roomies")

    resp = client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Groceries",
            "amount": "10.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "equal",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM expense_splits").fetchall()
        shares = {r["member_id"]: r["share_cents"] for r in rows}
    assert sum(shares.values()) == 1000
    assert set(shares.values()) <= {333, 334}


def test_add_expense_exact_split_rejects_mismatched_amounts(client, app):
    create_group_with_members(client, "Vacation", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Vacation")

    resp = client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Hotel",
            "amount": "100.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "exact",
            f"exact_{ids['Alice']}": "40.00",
            f"exact_{ids['Bob']}": "50.00",  # sums to 90, not 100
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Split validation failed" in resp.data

    with app.app_context():
        db = get_db()
        count = db.execute("SELECT COUNT(*) AS c FROM expenses").fetchone()["c"]
    assert count == 0


def test_add_expense_percentage_split_rejects_invalid_percentages(client, app):
    create_group_with_members(client, "Office", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Office")

    resp = client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Lunch",
            "amount": "50.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "percentage",
            f"pct_{ids['Alice']}": "60",
            f"pct_{ids['Bob']}": "60",  # sums to 120, not 100
        },
        follow_redirects=True,
    )
    assert b"Split validation failed" in resp.data

    with app.app_context():
        db = get_db()
        count = db.execute("SELECT COUNT(*) AS c FROM expenses").fetchone()["c"]
    assert count == 0


def test_add_expense_percentage_split_accepts_valid_percentages(client, app):
    create_group_with_members(client, "Cowork", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Cowork")

    resp = client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Supplies",
            "amount": "50.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "percentage",
            f"pct_{ids['Alice']}": "70",
            f"pct_{ids['Bob']}": "30",
        },
        follow_redirects=True,
    )
    assert b"Split validation failed" not in resp.data

    with app.app_context():
        db = get_db()
        rows = db.execute("SELECT * FROM expense_splits").fetchall()
        shares = {r["member_id"]: r["share_cents"] for r in rows}
    assert shares[ids["Alice"]] == 3500
    assert shares[ids["Bob"]] == 1500


def test_recording_settlement_updates_balances_to_zero(client, app):
    create_group_with_members(client, "Cabin", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Cabin")

    client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Cabin rental",
            "amount": "100.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "equal",
        },
        follow_redirects=True,
    )
    # At this point Bob owes Alice $50.

    resp = client.post(
        f"/groups/{group_id}/settlements",
        data={
            "from_member_id": str(ids["Bob"]),
            "to_member_id": str(ids["Alice"]),
            "amount_cents": "5000",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db = get_db()
        expenses = [dict(r) for r in db.execute(
            "SELECT * FROM expenses WHERE group_id = ?", (group_id,)
        )]
        splits = [dict(r) for r in db.execute(
            """SELECT expense_splits.* FROM expense_splits
               JOIN expenses ON expenses.id = expense_splits.expense_id
               WHERE expenses.group_id = ?""",
            (group_id,),
        )]
        settlements = [dict(r) for r in db.execute(
            "SELECT * FROM settlements WHERE group_id = ?", (group_id,)
        )]
        balances = compute_net_balances(
            [ids["Alice"], ids["Bob"]], expenses, splits, settlements
        )
    assert balances[ids["Alice"]] == 0
    assert balances[ids["Bob"]] == 0


def test_group_page_shows_all_settled_up_when_contributions_are_equal(client, app):
    create_group_with_members(client, "Fair Split", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Fair Split")

    client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Dinner",
            "amount": "20.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "equal",
        },
        follow_redirects=True,
    )
    client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Movie",
            "amount": "20.00",
            "paid_by_member_id": str(ids["Bob"]),
            "split_method": "equal",
        },
        follow_redirects=True,
    )

    resp = client.get(f"/groups/{group_id}")
    assert resp.status_code == 200
    assert b"All settled up" in resp.data


def test_group_detail_404_for_unknown_group(client):
    resp = client.get("/groups/9999")
    assert resp.status_code == 404


def test_create_group_rejects_empty_name(client):
    resp = client.post(
        "/groups", data={"name": "", "members": "Alice,Bob"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert b"Group name is required" in resp.data


def test_add_expense_rejects_negative_amount(client, app):
    create_group_with_members(client, "Negatives", ["Alice", "Bob"])
    group_id, ids = get_group_and_member_ids(app, "Negatives")

    resp = client.post(
        f"/groups/{group_id}/expenses",
        data={
            "description": "Refund?",
            "amount": "-20.00",
            "paid_by_member_id": str(ids["Alice"]),
            "split_method": "equal",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Amount must be positive" in resp.data

    with app.app_context():
        db = get_db()
        count = db.execute(
            "SELECT COUNT(*) AS c FROM expenses WHERE group_id = ?", (group_id,)
        ).fetchone()["c"]
    assert count == 0
