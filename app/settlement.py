"""Core money-splitting and debt-simplification algorithms.

All money amounts in this module are integer CENTS to avoid floating point
rounding errors creeping into anyone's bill. Convert to/from dollar strings
only at the presentation layer (see ``dollars_to_cents`` / ``cents_to_dollars``).

This module has no dependency on Flask or SQLite on purpose: it is plain,
easily-testable business logic.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def dollars_to_cents(value) -> int:
    """Convert a dollar amount (str, float, int, Decimal) to integer cents."""
    d = Decimal(str(value))
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def cents_to_dollars(cents: int) -> str:
    """Format integer cents as a dollar string, e.g. 1250 -> '12.50'."""
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents))
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def split_equal(amount_cents: int, member_ids) -> dict:
    """Split ``amount_cents`` equally among ``member_ids``.

    Rounding rule: integer-divide the amount by the number of members to get
    a base share for everyone, then hand out the leftover cents
    (``amount_cents % n``) one cent at a time, to members in ascending
    member_id order, starting from the smallest id. This guarantees:

    * the shares always sum EXACTLY to ``amount_cents`` (no cent is lost or
      invented by floating point rounding), and
    * nobody's share differs from anyone else's by more than one cent.

    Example: splitting $10.00 (1000 cents) three ways gives 334/333/333
    cents, with member id 1 (the smallest id) getting the extra cent.
    """
    if not member_ids:
        raise ValueError("cannot split among zero members")
    ordered = sorted(set(member_ids))
    n = len(ordered)
    base, remainder = divmod(amount_cents, n)
    shares = {}
    for i, mid in enumerate(ordered):
        shares[mid] = base + (1 if i < remainder else 0)
    return shares


def validate_exact_split(amount_cents: int, shares: dict) -> bool:
    """True iff the exact per-member shares sum exactly to the total."""
    return sum(shares.values()) == amount_cents


def split_exact(amount_cents: int, shares: dict) -> dict:
    """Validate and return an exact custom split.

    Raises ValueError if the provided shares do not sum exactly to the
    expense amount -- an exact split must add up, or it is rejected.
    """
    if not validate_exact_split(amount_cents, shares):
        raise ValueError(
            f"exact shares sum to {sum(shares.values())} cents, "
            f"expected {amount_cents} cents"
        )
    return dict(shares)


def validate_percentage_split(percentages: dict, tolerance: float = 1e-6) -> bool:
    """True iff the percentages sum to 100 (within a small float tolerance)."""
    return abs(sum(percentages.values()) - 100.0) <= tolerance


def split_percentage(amount_cents: int, percentages: dict) -> dict:
    """Split ``amount_cents`` by percentage.

    Raises ValueError if the percentages do not sum to 100.

    Uses the largest-remainder (Hamilton) method: each member's raw share is
    ``amount_cents * pct / 100``; we take the floor of every raw share, then
    hand out the leftover cents to the members with the largest fractional
    remainder first (ties broken by ascending member_id). This guarantees
    the resulting integer shares sum EXACTLY to ``amount_cents``, the same
    exactness guarantee as ``split_equal``.
    """
    if not validate_percentage_split(percentages):
        raise ValueError(
            f"percentages sum to {sum(percentages.values())}, expected 100"
        )
    ordered = sorted(percentages.keys())
    raw = {
        mid: Decimal(amount_cents) * Decimal(str(percentages[mid])) / Decimal(100)
        for mid in ordered
    }
    floor_shares = {mid: int(raw[mid]) for mid in ordered}  # truncates toward 0
    remainder = amount_cents - sum(floor_shares.values())

    by_largest_fraction = sorted(
        ordered, key=lambda mid: (-(raw[mid] - floor_shares[mid]), mid)
    )
    shares = dict(floor_shares)
    for mid in by_largest_fraction[:remainder]:
        shares[mid] += 1
    return shares


def compute_net_balances(member_ids, expenses, splits, settlements=None) -> dict:
    """Compute each member's net balance, in cents.

    Positive balance -> member is owed money overall (net creditor).
    Negative balance -> member owes money overall (net debtor).
    Zero            -> member is settled up.

    :param member_ids: iterable of member ids that should appear in the
        result (even if they have no expenses at all -> balance 0).
    :param expenses: iterable of dicts/rows with keys ``amount_cents`` and
        ``paid_by_member_id``.
    :param splits: iterable of dicts/rows with keys ``member_id`` and
        ``share_cents`` (each split row says "this member owes this much
        towards some expense").
    :param settlements: optional iterable of dicts/rows with keys
        ``from_member_id``, ``to_member_id``, ``amount_cents`` and
        ``completed``. Only rows where ``completed`` is truthy affect the
        balance -- a completed settlement means the debtor actually paid the
        creditor, so it nudges both balances toward zero.
    """
    balances = {mid: 0 for mid in member_ids}
    for exp in expenses:
        payer = exp["paid_by_member_id"]
        balances[payer] = balances.get(payer, 0) + exp["amount_cents"]
    for sp in splits:
        member = sp["member_id"]
        balances[member] = balances.get(member, 0) - sp["share_cents"]
    if settlements:
        for s in settlements:
            if not s.get("completed"):
                continue
            frm, to, amt = s["from_member_id"], s["to_member_id"], s["amount_cents"]
            balances[frm] = balances.get(frm, 0) + amt
            balances[to] = balances.get(to, 0) - amt
    return balances


def simplify_debts(balances: dict):
    """Greedy debt-simplification: minimum-ish number of settlements.

    Repeatedly matches the LARGEST creditor (most owed) with the LARGEST
    debtor (owes the most), settles the smaller of the two amounts between
    them, and repeats until every balance is zero. This is the classic
    greedy heuristic used by apps like Splitwise. It is provably optimal
    whenever no proper subset of the non-zero balances happens to sum to
    zero on its own (in which case it produces exactly n-1 transactions for
    n non-zero balances, which is the theoretical floor for that case); it
    can also discover and exploit zero-sum subsets ahead of a naive
    "everyone pays one person" approach. The fully general minimum-
    transactions problem is NP-hard (it's equivalent to a subset-sum/
    partition problem), so this greedy heuristic is the standard practical
    approach rather than an exhaustive search.

    :param balances: dict of member_id -> net balance in cents.
    :return: list of (from_member_id, to_member_id, amount_cents) tuples,
        each meaning "from_member_id should pay to_member_id amount_cents".
        Applying every transaction (debtor's balance += amount, creditor's
        balance -= amount) zeroes out every balance.
    """
    creditors = [[mid, bal] for mid, bal in balances.items() if bal > 0]
    debtors = [[mid, -bal] for mid, bal in balances.items() if bal < 0]

    transactions = []
    while creditors and debtors:
        creditors.sort(key=lambda x: x[1], reverse=True)
        debtors.sort(key=lambda x: x[1], reverse=True)
        cred_id, cred_amt = creditors[0]
        debt_id, debt_amt = debtors[0]
        amt = min(cred_amt, debt_amt)
        if amt > 0:
            transactions.append((debt_id, cred_id, amt))
        creditors[0][1] -= amt
        debtors[0][1] -= amt
        if creditors[0][1] == 0:
            creditors.pop(0)
        if debtors[0][1] == 0:
            debtors.pop(0)
    return transactions
