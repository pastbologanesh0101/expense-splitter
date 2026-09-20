# Expense Splitter

A small Splitwise-style **group** expense-splitting app: create a group,
add members, log shared expenses with flexible split methods, and get back
a settlement plan that uses the *minimum* number of payments needed to
settle everyone up.

Built with Flask + SQLite (no ORM, no JS framework) using an app-factory
pattern so the whole thing is easy to test.

## Why this is not a personal expense tracker

This app splits costs **among multiple people** and tracks who-owes-whom
across a whole group, then computes a debt-settlement plan. That's a
fundamentally different problem (and a different core algorithm — graph/
greedy debt simplification) from a single-user tracker that just tags your
own purchases by category and totals them per month.

## Features

- Create groups and add members
- Log an expense with one of three split methods:
  - **Equal** — split evenly across every member
  - **Exact** — specify exactly how much each member owes (must sum to the
    total, or the expense is rejected)
  - **Percentage** — specify what percentage each member owes (must sum to
    100%, or the expense is rejected)
- View each member's net balance (owed money / owes money / settled up)
- View a computed **settlement plan**: the minimum set of "X pays Y $Z"
  transactions that zeroes out every balance in the group
- Mark a settlement as paid, which is immediately reflected in every
  member's recomputed balance

## The debt-simplification algorithm

Splitting expenses produces a *net balance* per member: positive means
"is owed money", negative means "owes money". Naively, you could just have
every debtor pay the group organizer and have the organizer pay out
whoever's still owed money — but that produces far more transactions than
necessary, and it isn't how real settle-up tools work.

Instead, `app/settlement.py::simplify_debts` implements the classic
**greedy debt-simplification heuristic** (the same idea Splitwise uses):

1. Compute everyone's net balance across all expenses (see below).
2. While there's still a creditor (balance > 0) and a debtor (balance < 0)
   left:
   - Find the member owed the **most** money (largest creditor) and the
     member who owes the **most** money (largest debtor).
   - Have the debtor pay the creditor `min(creditor's balance, debtor's
     debt)`.
   - Reduce both balances by that amount; drop whichever one hits zero.
3. Repeat until every balance is zero.

Each step always drives at least one balance to exactly zero, so for `n`
non-zero balances this never needs more than `n - 1` transactions — and
whenever a subset of balances happens to sum to zero on its own, the
algorithm finds and exploits that, needing even fewer. (Finding the
*globally* fewest possible transactions in every case is an NP-hard
partition problem; the greedy largest-vs-largest heuristic is the standard,
practical answer used by real settle-up apps.)

### Worked example

Alice pays $30 for dinner, Bob pays $15 for a cab, split equally three ways
(Alice, Bob, Cara) each time:

| Expense | Amount | Paid by | Alice owes | Bob owes | Cara owes |
|---|---|---|---|---|---|
| Dinner | $30 | Alice | $10 | $10 | $10 |
| Cab | $15 | Bob | $5 | $5 | $5 |

Net balances:
- Alice: paid $30, owes $15 total → **+$15** (is owed $15)
- Bob: paid $15, owes $15 total → **$0** (settled)
- Cara: paid $0, owes $15 total → **-$15** (owes $15)

`simplify_debts` sees one creditor (Alice, +$15) and one debtor (Cara,
-$15) and outputs a single transaction:

> Cara pays Alice $15.00

Applying it: Alice's balance becomes `15 - 15 = 0`, Cara's becomes
`-15 + 15 = 0`. Bob was already at zero. Everyone is settled with the
minimum possible number of payments (1, not 2).

## Split methods and rounding rules

All money is stored internally as **integer cents** to avoid floating-point
rounding bugs; dollar strings are only used for display and form input.

- **Equal split**: `amount_cents // n` is everyone's base share; the
  leftover `amount_cents % n` cents are handed out one at a time to the
  members with the **lowest member IDs**, so the shares always sum exactly
  to the total and no one differs from anyone else by more than one cent.
  Example: $10.00 split three ways → $3.34 / $3.33 / $3.33 (lowest ID gets
  the extra cent).
- **Exact split**: you specify a dollar amount per member; rejected (with a
  visible error, no expense recorded) if the amounts don't sum exactly to
  the total.
- **Percentage split**: you specify a percentage per member; rejected if
  the percentages don't sum to 100%. Shares are computed with the
  **largest-remainder (Hamilton) method** — floor everyone's raw share,
  then give the leftover cents to whoever had the largest fractional
  remainder — so the resulting integer shares still sum exactly to the
  total even when the percentages don't divide evenly (e.g. 33.33/33.33/
  33.34%).

## Running it

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

Then open http://127.0.0.1:5000 in your browser. Data is stored in
`instance/expense_splitter.db` (SQLite), created automatically on first
run.

## Running the tests

```bash
pip install -r requirements.txt
pytest -v
```

Tests use `create_app(testing=True)`, which points the app at an in-memory
SQLite database, plus Flask's test client — no external services needed.

Continuous integration (`.github/workflows/tests.yml`) runs the same suite
on Python 3.11 and 3.12 on every push and pull request.

## Project layout

```
app/
  __init__.py      # create_app() app factory
  db.py            # sqlite3 connection handling + schema
  settlement.py    # split calculations + debt-simplification (pure logic)
  routes.py        # Flask views
  templates/       # Jinja2 templates
  static/style.css
tests/
  test_settlement.py  # algorithm unit tests
  test_app.py         # Flask test-client integration tests
run.py             # dev server entry point
```

## Troubleshooting / FAQ

**My exact/percentage split gets rejected even though the numbers "look
right" to me.**
Exact splits must sum to *exactly* the expense's total in cents, and
percentage splits must sum to *exactly* 100 (within a tiny float
tolerance). If you typed shares like `33.33 / 33.33 / 33.33` for a
percentage split, that's `99.99`, not `100` — use `33.33 / 33.33 / 33.34`
instead, or just use the **Equal** split method, which handles the
leftover-cent rounding for you automatically.

**The settlement plan suggests a payment I didn't expect.**
`simplify_debts` doesn't try to preserve "who originally paid whom" — it
only cares about final net balances, and always matches the current
largest creditor with the current largest debtor. If two members happen to
have the same absolute balance, the specific pairing can look different
from expense to expense even though the total number of payments is still
minimal.

**Can I record a settlement from a member to themselves?**
No — `record_settlement` explicitly rejects `from_member_id ==
to_member_id` with "A member cannot record a settlement paid to
themselves." to avoid corrupting the group's balance history.

**Why does the app store cents instead of dollars?**
Storing `amount_cents` as an integer avoids binary floating-point rounding
errors (e.g. `0.1 + 0.2 != 0.3` in IEEE-754 doubles) that would otherwise
accumulate across many expenses and eventually leave a settlement plan a
cent or two off. Dollar strings only exist at the form-input/display
boundary (`dollars_to_cents` / `cents_to_dollars`).
