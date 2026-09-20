from __future__ import annotations

from datetime import datetime, timezone
from decimal import InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import settlement as ss
from .db import get_db

bp = Blueprint("main", __name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@bp.route("/")
def index():
    db = get_db()
    groups = db.execute("SELECT * FROM groups ORDER BY id DESC").fetchall()
    return render_template("index.html", groups=groups)


@bp.route("/groups", methods=["POST"])
def create_group():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Group name is required.", "error")
        return redirect(url_for("main.index"))

    db = get_db()
    cur = db.execute(
        "INSERT INTO groups (name, created_at) VALUES (?, ?)", (name, now_iso())
    )
    group_id = cur.lastrowid

    member_names = [n.strip() for n in request.form.get("members", "").split(",") if n.strip()]
    for member_name in member_names:
        db.execute(
            "INSERT INTO members (group_id, name) VALUES (?, ?)", (group_id, member_name)
        )
    db.commit()
    return redirect(url_for("main.group_detail", group_id=group_id))


def _get_group_or_404(db, group_id):
    group = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    if group is None:
        abort(404)
    return group


def _load_group_data(db, group_id):
    members = db.execute(
        "SELECT * FROM members WHERE group_id = ? ORDER BY id", (group_id,)
    ).fetchall()
    expenses = db.execute(
        "SELECT * FROM expenses WHERE group_id = ? ORDER BY id DESC", (group_id,)
    ).fetchall()
    splits = db.execute(
        """SELECT expense_splits.* FROM expense_splits
           JOIN expenses ON expenses.id = expense_splits.expense_id
           WHERE expenses.group_id = ?""",
        (group_id,),
    ).fetchall()
    settlements = db.execute(
        "SELECT * FROM settlements WHERE group_id = ? ORDER BY id DESC", (group_id,)
    ).fetchall()
    return members, expenses, splits, settlements


@bp.route("/groups/<int:group_id>")
def group_detail(group_id):
    db = get_db()
    group = _get_group_or_404(db, group_id)
    members, expenses, splits, settlements = _load_group_data(db, group_id)
    member_ids = [m["id"] for m in members]
    names = {m["id"]: m["name"] for m in members}

    balances = ss.compute_net_balances(
        member_ids,
        [dict(e) for e in expenses],
        [dict(s) for s in splits],
        [dict(s) for s in settlements],
    )
    plan = ss.simplify_debts(balances)

    balances_view = [
        {"id": mid, "name": names.get(mid, "?"), "cents": bal, "dollars": ss.cents_to_dollars(bal)}
        for mid, bal in sorted(balances.items(), key=lambda item: -item[1])
    ]
    plan_view = [
        {
            "from_id": frm,
            "from_name": names.get(frm, "?"),
            "to_id": to,
            "to_name": names.get(to, "?"),
            "amount_cents": amt,
            "amount": ss.cents_to_dollars(amt),
        }
        for frm, to, amt in plan
    ]

    splits_by_expense: dict[int, list] = {}
    for s in splits:
        splits_by_expense.setdefault(s["expense_id"], []).append(s)

    expenses_view = [
        {
            "id": e["id"],
            "description": e["description"],
            "amount": ss.cents_to_dollars(e["amount_cents"]),
            "paid_by": names.get(e["paid_by_member_id"], "?"),
            "split_method": e["split_method"],
            "created_at": e["created_at"],
            "shares": [
                {
                    "name": names.get(s["member_id"], "?"),
                    "amount": ss.cents_to_dollars(s["share_cents"]),
                }
                for s in splits_by_expense.get(e["id"], [])
            ],
        }
        for e in expenses
    ]

    settlements_view = [
        {
            "id": s["id"],
            "from_name": names.get(s["from_member_id"], "?"),
            "to_name": names.get(s["to_member_id"], "?"),
            "amount": ss.cents_to_dollars(s["amount_cents"]),
            "completed": bool(s["completed"]),
            "created_at": s["created_at"],
        }
        for s in settlements
    ]

    return render_template(
        "group.html",
        group=group,
        members=members,
        expenses=expenses_view,
        balances=balances_view,
        plan=plan_view,
        settlements=settlements_view,
    )


@bp.route("/groups/<int:group_id>/members", methods=["POST"])
def add_member(group_id):
    db = get_db()
    _get_group_or_404(db, group_id)
    name = request.form.get("name", "").strip()
    if name:
        db.execute("INSERT INTO members (group_id, name) VALUES (?, ?)", (group_id, name))
        db.commit()
    else:
        flash("Member name is required.", "error")
    return redirect(url_for("main.group_detail", group_id=group_id))


@bp.route("/groups/<int:group_id>/expenses", methods=["POST"])
def add_expense(group_id):
    db = get_db()
    _get_group_or_404(db, group_id)
    members = db.execute("SELECT * FROM members WHERE group_id = ?", (group_id,)).fetchall()
    member_ids = [m["id"] for m in members]

    description = request.form.get("description", "").strip()
    amount_str = request.form.get("amount", "").strip()
    paid_by = request.form.get("paid_by_member_id", type=int)
    split_method = request.form.get("split_method", "equal")

    if not description or not amount_str or paid_by not in member_ids:
        flash("Description, amount, and a valid payer are required.", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    try:
        amount_cents = ss.dollars_to_cents(amount_str)
    except (InvalidOperation, ValueError):
        flash("Invalid amount.", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    if amount_cents <= 0:
        flash("Amount must be positive.", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    try:
        if split_method == "equal":
            shares = ss.split_equal(amount_cents, member_ids)
        elif split_method == "exact":
            raw = {}
            for mid in member_ids:
                value = request.form.get(f"exact_{mid}", "").strip()
                raw[mid] = ss.dollars_to_cents(value) if value else 0
            shares = ss.split_exact(amount_cents, raw)
        elif split_method == "percentage":
            raw = {}
            for mid in member_ids:
                value = request.form.get(f"pct_{mid}", "").strip()
                raw[mid] = float(value) if value else 0.0
            shares = ss.split_percentage(amount_cents, raw)
        else:
            flash("Unknown split method.", "error")
            return redirect(url_for("main.group_detail", group_id=group_id))
    except (ValueError, InvalidOperation) as exc:
        flash(f"Split validation failed: {exc}", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    cur = db.execute(
        """INSERT INTO expenses
               (group_id, description, amount_cents, paid_by_member_id, split_method, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (group_id, description, amount_cents, paid_by, split_method, now_iso()),
    )
    expense_id = cur.lastrowid
    for mid, share_cents in shares.items():
        db.execute(
            "INSERT INTO expense_splits (expense_id, member_id, share_cents) VALUES (?, ?, ?)",
            (expense_id, mid, share_cents),
        )
    db.commit()
    flash("Expense added.", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))


@bp.route("/groups/<int:group_id>/settlements", methods=["POST"])
def record_settlement(group_id):
    db = get_db()
    _get_group_or_404(db, group_id)

    from_id = request.form.get("from_member_id", type=int)
    to_id = request.form.get("to_member_id", type=int)
    amount_raw = request.form.get("amount_cents") or request.form.get("amount")

    amount_cents = None
    if amount_raw is not None:
        try:
            amount_cents = int(amount_raw)
        except ValueError:
            try:
                amount_cents = ss.dollars_to_cents(amount_raw)
            except (InvalidOperation, ValueError):
                amount_cents = None

    if not from_id or not to_id or not amount_cents or amount_cents <= 0:
        flash("Invalid settlement.", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    if from_id == to_id:
        flash("A member cannot record a settlement paid to themselves.", "error")
        return redirect(url_for("main.group_detail", group_id=group_id))

    db.execute(
        """INSERT INTO settlements
               (group_id, from_member_id, to_member_id, amount_cents, completed, created_at, completed_at)
           VALUES (?, ?, ?, ?, 1, ?, ?)""",
        (group_id, from_id, to_id, amount_cents, now_iso(), now_iso()),
    )
    db.commit()
    flash("Settlement recorded.", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))
