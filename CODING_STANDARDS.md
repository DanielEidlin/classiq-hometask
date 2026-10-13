# Coding standards

Conventions for code in this repository, drawn from review. Tooling already enforces formatting and `mypy --strict`; these cover what it can't.

## Names

- Names are readable and specific: they say what the thing is or does, without abbreviations. `quantum_circuit`, not `qc`; `get_db_connection`, not `get_connection`; `ComposeStack`, not `Stack`.
- Use the domain vocabulary in `CONTEXT.md` (circuit, task, counts, attempt).
- When an external contract fixes a terse name (the API's `qc` field), keep it at the boundary only and map it to a readable name with an alias.
- A library's own idioms stay as they are (SQLAlchemy's `table.c`).

## Structure

- No nested functions. Write a private module-level function (`_name`) and bind arguments with `functools.partial` where needed.
- One module per FastAPI router (`api/tasks.py`, `api/health.py`). `api/main.py` only builds the app, registers routers and installs error handling.
- Each database statement lives in its own small function named after what it does (`_claim`, `_complete`, `_fetch_outcome`), returning a typed result. Write the query out in full inside it; pass several conditions to `.where(...)` rather than building `sa.and_(...)` in a separate variable.

## Dependencies and state

- Code receives its collaborators; it doesn't reach for globals.
  - In the API, routes declare FastAPI dependencies (`api/dependencies.py`), using the `Annotated[..., Depends(...)]` shorthands (`DbConnectionDep`, `PublisherDep`). Resources that need async setup and teardown are opened by the lifespan and yielded as lifespan state, typed by `AppState`; nothing writes to `app.state`.
  - Classes take their collaborators in the constructor, so tests can pass their own. A classmethod builds the production instance (`TaskPublisher.connect(url)`).
- Module-level objects are fine when building them has no side effects (a SQLAlchemy `Table`, a router). Anything that opens connections or does I/O is created at startup or on first use, never at import. A connection pool that must not cross a `fork` is created lazily (`functools.cache`).

## Configuration

- Every limit and timing is an environment variable with a default, read in one place in code and listed in `compose.yaml`. Don't set the same value a second time elsewhere (a Dockerfile `CMD` flag, a hard-coded default in another module).
- A value read once at startup is a module constant (`SHOTS = int(os.environ.get("SHOTS", "1024"))`), not a function.

## Types and data

- Pydantic models at every boundary:
  - Requests and responses are models, one per response shape. Routes return models, never dicts, and the return annotation is the response model.
  - Rows read from the database are validated into models where they are loaded. When the shape depends on a status, use a discriminated union keyed on it, so invalid stored data is rejected at load.
- Fixed values are `Literal` fields with a default (`status: Literal["pending"] = "pending"`, fixed response messages), not module constants passed in by the caller.
- Never use `assert` to check runtime data: it is stripped under `python -O` and raises a bare `AssertionError`. Validate the data where it enters instead. Use `match` with `case _: assert_never(...)` so mypy checks that every case is handled.
- `cast` is acceptable where a library's value is untyped; prefer it to a throwaway annotated local.

## Errors and logging

- Routes signal expected errors by raising `HTTPException`. One exception handler renders every error in the `{"status": "error", "message": ...}` shape and logs it. Unexpected exceptions become a 500 in the same shape, logged once with the traceback.
- Log level follows cause: expected client outcomes (unknown ID, malformed body) at `info`; faults at `warning`/`error`, with the traceback (`exc_info`) logged where the exception is caught.
- Log events are `snake_case` names with structured fields. Every task-related line carries `task_id`.
- Use `raise ... from None` when the original exception is fully explained by the new one.

## Comments

- No comments or docstrings that restate the code or narrate what the next lines do. A comment says why: a non-obvious constraint, a trade-off, or a library behaviour the reader wouldn't expect. Keep it short.

## Tests

- No magic numbers. Name the value as a constant, or import the existing one. A black-box test that can't import the source package defines its own constant and says where the value comes from.
- Assertions hold for every valid outcome. Where results are random, assert what is always true (a subset of keys, the total), and say so.
- Expensive fixtures (the Compose stack, its HTTP client) are session-scoped.
- Tests are synchronous unless a test needs concurrency. An async test can then sit beside the sync ones with its own client fixture.
- To change a dependency of the API in-process, override it with `app.dependency_overrides` and use `TestClient` without its lifespan, so no database or broker is needed.
- Fault-injection tests synchronise on observable state with a generous deadline, never on fixed sleeps, and restore the stack in a `finally` block.

## Docker

- Each Dockerfile copies and installs only the workspace members its image needs.
