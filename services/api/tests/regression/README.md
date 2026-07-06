# Backend Regression Fixtures

Regression tests in this directory preserve fixed backend defects as JSON reproduction fixtures.
Every fixed bug must add or update a regression fixture and a regression assertion before the
fix is considered complete.

## Regression test naming convention

- Fixture IDs use lower snake case: `stage<phase>_<short_defect_name>`.
- Fixture files live in `fixtures/<fixture_id>.json`.
- Test names include `test_regression_` and the fixture ID should appear in either the
  parametrized case ID or the assertion helper name.
- Stage-scoped fixtures keep the original stage prefix when they capture a bug found during that
  stage, for example `stage10_end_turn_phase_graph`.

## JSON reproduction fixture format

Each fixture is a JSON object with these required fields:

- `fixture_version`: integer format version. Use `1` until this README documents a new version.
- `id`: exact fixture ID and filename stem.
- `title`: short human-readable defect title.
- `source`: review, smoke, issue, or test artifact that discovered the defect.
- `seed`: seed passed to `create_initial_game_state`.
- `game_id`: deterministic replay game ID.
- `players`: list of 2-5 player setup objects with `id`, `name`, and `kind`.
- `event_log`: accepted event list. The normal regression harness replays from seed and event log
  using the real reducer and event models.
- `reproduction`: data needed to submit or inspect the bad action, trigger, or replay prefix.
- `expectation`: fixed behavior assertions, including the reason code or final state fields.

The fixture event log must be contiguous from sequence `1`, must validate through
`GameEvent.model_validate`, and must replay through `replay_events`. Rejected actions are described
in `reproduction` because rejected actions are not accepted reducer events.

## Maintenance rule

Every fixed bug must either add a new fixture or update an existing fixture/test in this directory.
The test must replays from seed and event log, assert the bug remains fixed, and document any
external source when the original failure was found outside the backend test suite.
