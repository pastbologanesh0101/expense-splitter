# Contributing to Expense Splitter

This is a Splitwise-style group expense splitter built with Flask and
SQLite. The core value of this project is the money math (splitting,
rounding, debt simplification), so changes there deserve extra care.

## Getting set up

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python run.py
```

## Running the tests

```bash
pytest -v
```

There are two test files with different responsibilities:

- `tests/test_settlement.py` — pure unit tests for the algorithms in
  `app/settlement.py` (splitting, rounding, debt simplification). No Flask
  or database involved. Add tests here for any change to the math itself.
- `tests/test_app.py` — Flask test-client integration tests covering
  routes, form validation, and the database layer end to end.

Any change to `app/settlement.py` should come with a `test_settlement.py`
case, and any change to a route in `app/routes.py` should come with a
`test_app.py` case.

## Code style

- All money is handled as **integer cents** internally
  (`dollars_to_cents` / `cents_to_dollars`); never introduce a `float` for
  a dollar amount that gets stored or summed — that reopens the rounding
  bugs this project was written to avoid.
- Keep `app/settlement.py` free of Flask/SQLite imports — it should stay
  pure, easily-testable business logic.
- Use `flash()` for user-facing errors, and make messages specific enough
  that someone filling out the form knows exactly what to fix.

## Submitting changes

1. Open an issue or PR describing the change and why it's useful.
2. Run `pytest -v` locally — it must pass before you push.
3. Keep commits small and focused, with a clear one-line description of
   what changed.
