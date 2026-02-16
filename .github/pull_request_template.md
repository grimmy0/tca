## Summary

- What changed:
- Why:

## Plan Traceability

- [ ] Every completed acceptance criterion (`[x]`) in `docs/implementation-plan.md` includes explicit `[Tests: tests/...::test_...]` mappings.
- [ ] `uv run python scripts/validate_plan_criteria.py --run-tests` passes locally.

## Contract Checks

- [ ] All route decorators in `tca/api/routes/` include explicit `response_model=...`.
- [ ] API contract tests pass for changed endpoints (`tests/api` and `tests/contracts/test_api_contracts.py`).
- [ ] Structured logging invariants are preserved (`tests/logging` and `tests/contracts/test_logging_contracts.py`).

## Lifespan and Startup Safety

- [ ] `TestClient` is used via context manager (`with TestClient(app) as client:`) in API/app tests.
- [ ] Lifespan startup/shutdown behavior is covered for changed app factory code.
- [ ] Missing startup dependency fail-fast path is covered for changed lifespan code.

## Verification

- [ ] `./scripts/lint_strict.sh`
- [ ] `uv run pytest -q`

## Notes

- Any intentionally deferred risk or follow-up:
