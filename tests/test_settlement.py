"""Unit tests for the pure debt-splitting / debt-simplification algorithms.

These tests call the ``app.settlement`` module directly -- no Flask app or
database needed, since that module has no framework dependency.
"""
from app import settlement as ss


def test_split_equal_divides_evenly_with_no_remainder():
    shares = ss.split_equal(9000, [1, 2, 3])  # $90.00 / 3 people
    assert shares == {1: 3000, 2: 3000, 3: 3000}
    assert sum(shares.values()) == 9000


def test_split_equal_distributes_remainder_cents_to_lowest_ids():
    # $10.00 across 3 people = 333.33... cents each. Our documented rounding
    # rule: give the leftover cent(s) to the lowest member_ids first.
    shares = ss.split_equal(1000, [3, 1, 2])
    assert shares[1] == 334
    assert shares[2] == 333
    assert shares[3] == 333
    assert sum(shares.values()) == 1000


def test_split_exact_accepts_matching_sum():
    shares = ss.split_exact(1000, {1: 400, 2: 600})
    assert shares == {1: 400, 2: 600}


def test_split_exact_rejects_mismatched_sum():
    assert ss.validate_exact_split(1000, {1: 400, 2: 550}) is False
    try:
        ss.split_exact(1000, {1: 400, 2: 550})
        assert False, "expected ValueError for a mismatched exact split"
    except ValueError:
        pass


def test_split_percentage_rejects_non_100_total():
    assert ss.validate_percentage_split({1: 50, 2: 40}) is False
    try:
        ss.split_percentage(1000, {1: 50, 2: 40})
        assert False, "expected ValueError for percentages that don't sum to 100"
    except ValueError:
        pass


def test_split_percentage_sums_exactly_despite_rounding():
    # 33.33 + 33.33 + 33.34 = 100.00, but naive rounding of each share could
    # drop a cent. The largest-remainder method must still land on exactly
    # 1000 cents total.
    shares = ss.split_percentage(1000, {1: 33.33, 2: 33.33, 3: 33.34})
    assert sum(shares.values()) == 1000


def test_compute_net_balances_multiple_expenses_different_payers():
    member_ids = [1, 2, 3]
    expenses = [
        {"id": 1, "amount_cents": 3000, "paid_by_member_id": 1},
        {"id": 2, "amount_cents": 1500, "paid_by_member_id": 2},
    ]
    splits = [
        {"expense_id": 1, "member_id": 1, "share_cents": 1000},
        {"expense_id": 1, "member_id": 2, "share_cents": 1000},
        {"expense_id": 1, "member_id": 3, "share_cents": 1000},
        {"expense_id": 2, "member_id": 1, "share_cents": 500},
        {"expense_id": 2, "member_id": 2, "share_cents": 500},
        {"expense_id": 2, "member_id": 3, "share_cents": 500},
    ]
    balances = ss.compute_net_balances(member_ids, expenses, splits)
    # member 1 paid 3000 total, owes 1500 total share -> net +1500
    # member 2 paid 1500 total, owes 1500 total share -> net 0
    # member 3 paid 0, owes 1500 total share -> net -1500
    assert balances == {1: 1500, 2: 0, 3: -1500}
    assert sum(balances.values()) == 0


def test_simplify_debts_zeroes_out_balances_when_applied():
    balances = {1: 1500, 2: 0, 3: -1500}
    plan = ss.simplify_debts(balances)
    result = dict(balances)
    for frm, to, amt in plan:
        result[frm] += amt
        result[to] -= amt
    assert all(v == 0 for v in result.values())


def test_simplify_debts_minimum_transactions_known_scenario():
    # A and B are each owed $10; C and D each owe $10. There's a zero-sum
    # pairing (A<->C, B<->D), so the true minimum is 2 transactions, not the
    # naive "route everything through one person" answer of 3.
    balances = {"A": 1000, "B": 1000, "C": -1000, "D": -1000}
    plan = ss.simplify_debts(balances)
    assert len(plan) == 2

    result = dict(balances)
    for frm, to, amt in plan:
        result[frm] += amt
        result[to] -= amt
    assert all(v == 0 for v in result.values())


def test_simplify_debts_no_zero_sum_subset_needs_n_minus_1_transactions():
    # No pair of these four balances sums to zero, so the theoretical floor
    # is n-1 = 3 transactions, and the greedy algorithm should hit it.
    balances = {"A": 4000, "B": 2000, "C": -3000, "D": -3000}
    plan = ss.simplify_debts(balances)
    assert len(plan) == 3
    result = dict(balances)
    for frm, to, amt in plan:
        result[frm] += amt
        result[to] -= amt
    assert all(v == 0 for v in result.values())


def test_simplify_debts_no_transactions_when_already_balanced():
    balances = {1: 0, 2: 0, 3: 0}
    assert ss.simplify_debts(balances) == []


def test_recording_a_completed_settlement_updates_balances_to_zero():
    member_ids = [1, 2, 3]
    expenses = [{"id": 1, "amount_cents": 3000, "paid_by_member_id": 1}]
    splits = [
        {"expense_id": 1, "member_id": 1, "share_cents": 1000},
        {"expense_id": 1, "member_id": 2, "share_cents": 1000},
        {"expense_id": 1, "member_id": 3, "share_cents": 1000},
    ]
    balances_before = ss.compute_net_balances(member_ids, expenses, splits)
    assert balances_before == {1: 2000, 2: -1000, 3: -1000}

    settlements = [
        {"from_member_id": 2, "to_member_id": 1, "amount_cents": 1000, "completed": True},
        {"from_member_id": 3, "to_member_id": 1, "amount_cents": 1000, "completed": False},
    ]
    balances_after = ss.compute_net_balances(member_ids, expenses, splits, settlements)
    # Only the completed settlement (member 2 -> member 1) should count.
    assert balances_after == {1: 1000, 2: 0, 3: -1000}


def test_group_with_all_equal_contributions_needs_zero_settlements():
    member_ids = [1, 2]
    expenses = [
        {"id": 1, "amount_cents": 2000, "paid_by_member_id": 1},
        {"id": 2, "amount_cents": 2000, "paid_by_member_id": 2},
    ]
    splits = [
        {"expense_id": 1, "member_id": 1, "share_cents": 1000},
        {"expense_id": 1, "member_id": 2, "share_cents": 1000},
        {"expense_id": 2, "member_id": 1, "share_cents": 1000},
        {"expense_id": 2, "member_id": 2, "share_cents": 1000},
    ]
    balances = ss.compute_net_balances(member_ids, expenses, splits)
    assert balances == {1: 0, 2: 0}
    assert ss.simplify_debts(balances) == []


def test_dollars_cents_roundtrip():
    assert ss.dollars_to_cents("12.50") == 1250
    assert ss.cents_to_dollars(1250) == "12.50"
    assert ss.cents_to_dollars(-150) == "-1.50"
