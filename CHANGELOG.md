# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2025-09-19

Initial release.

### Added

- Flask application factory (`create_app()`) with SQLite storage (no ORM).
- Group and member management: create a group with a comma-separated
  member list, add members afterward.
- Expense logging with three split methods:
  - **Equal** — integer-cent division with leftover cents assigned to the
    lowest member IDs.
  - **Exact** — per-member dollar amounts that must sum exactly to the
    total or the expense is rejected.
  - **Percentage** — per-member percentages that must sum to 100, split
    using the largest-remainder (Hamilton) method for exact cent totals.
- `app/settlement.py`: pure, Flask/SQLite-free business logic for net
  balance computation (`compute_net_balances`) and greedy debt
  simplification (`simplify_debts`) that produces the minimum-ish set of
  "X pays Y" transactions to zero out a group's balances.
- Settlement recording (`record_settlement`), which marks a payment as
  completed and immediately affects every member's recomputed balance.
- Group detail page showing per-member balances, the computed settlement
  plan, and expense history with per-member shares.
- All money handled as integer cents internally to avoid floating-point
  rounding bugs; dollar strings only at the display/form boundary.
- Two-tier test suite: `tests/test_settlement.py` (pure algorithm unit
  tests) and `tests/test_app.py` (Flask test-client integration tests).
- GitHub Actions CI running the test suite on Python 3.11 and 3.12.
- MIT license.
