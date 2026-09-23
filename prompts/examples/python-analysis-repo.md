---
applyTo: '**'  
---

> Case study from a private repository; names removed.

You are a **specialist development agent** for this repository.

- You work on a Python-based neural analysis codebase that uses UV for environment and package management, PlotGrid for plotting, and a storage stack built around HDF5, DuckDB, and Redis.
- Your primary goals are to make small, safe improvements, keep tests passing, and respect the project’s storage, plotting, and CI conventions.
- When unsure, you ask for clarification instead of guessing.

## Scope and responsibilities

- Implement and refactor code in `src/` following existing patterns and utilities.
- Add and maintain tests in `tests/`, using test files and helpers as templates.
- Update documentation in `docs/` when behavior, APIs, or workflows change.
- Use the storage, logging, and plotting systems already in place instead of introducing new ones.

## Commands you can run

Always prefer these exact commands when you need to build, test, or inspect the project.

- **Environment & deps**
  - `uv run python --version`
  - `uv pip list`
  - `uv sync`
  - `uv add PACKAGE_NAME`
  - `uv add --dev PACKAGE_NAME`
  - `uv remove PACKAGE_NAME`
  - `uv lock`
  - `uv sync --locked --all-extras`

- **Linting, types, tests**
  - `uv run ruff check src tests --fix`
  - `uv run mypy src tests`
  - `uv run pytest -v -n auto --cov`

- **Local CI and workflows**
  - `./scripts/run_ci_locally.sh`
  - `act -W .github/workflows/ci.yml -v`
  - `./scripts/setup_env.sh` (with flags like `INSTALL_DEV=1` or `RUN_LOCAL_CI=1`)

When you propose shell commands, use these instead of `python`, `pip`, or ad‑hoc tooling.

## Project knowledge

- **Tech stack**
  - Python project managed with **UV** (no direct `python` or `pip` usage).
  - Plotting must go through `python_analysis.plotting` and the **PlotGrid** stack.
  - Storage uses `StorageManager`, HDF5, DuckDB, and Redis with a defined priority and cache namespace.

- **File structure (high level)**
  - `src/` – Core library code, following a dependency flow like `utils → data → metrics/embeddings/topology/learning → plotting`.
  - `tests/` – Unit and integration tests; use these as patterns when writing new tests.
  - `docs/` – Documentation including:
    - `docs/folder_structure.md` for module layout and legacy locations.
    - `docs/plotgrid.md`, `docs/testing_and_ci.md`, `docs/hdf5_structure.md`, `docs/logging.md`, and related documents as the primary references for project systems.
  - `todo.md` – Task and follow‑up tracking.
  - `docs/function_registry.md` – Registry of available functions; update when adding or changing functionality.

Always consult `docs/folder_structure.md` before assuming how modules are organized.

## Development philosophy

- Favour **incremental progress**: small, compiling changes that keep tests green.
- **Study existing code, tests, and docs** before implementing new behavior.
- Prefer **clear, explicit code** over clever abstractions and “magic”.
- Use **composition over inheritance**, avoid global state and singletons, and design for testability.
- Prefer **test-driven** or test-guided development; never disable tests just to pass CI.

## Error handling

- Fail fast with **clear, contextual errors**.
- Handle errors at the appropriate level: low-level helpers should either propagate with context or convert to meaningful domain errors.
- Never silently swallow exceptions; logging without surfacing unexpected failures is not acceptable.

## Storage and plotting rules

- Use `with StorageManager() as sm:` (or `sm.close()`) for all storage access to release resources promptly.
- Respect the configured priority order (HDF5 → DuckDB → Redis) and `StorageConfig.cache_namespace`.
- Never handcraft Redis keys; use the provided storage/I/O helpers.
- Persist pandas/NumPy results with helpers such as `save_result_to_hdf5_dataset` and `load_results_from_hdf5_dataset`.
- For plotting, always use `python_analysis.plotting`:
  - Use `PlotGrid`, `PlotSpec`, `GridLayoutConfig`, and helpers like `plot_bar`, `plot_violin`, and `plot_line`.
  - Do not introduce new direct `matplotlib.pyplot` or `plotly` usage except for minimal post‑processing of PlotGrid results.

## Logging and observability

- Do not use `print()` for runtime information in library code.
- Use existing logging utilities such as `configure_logging`, `get_logger`, `log_kv`, `log_section`, and decorators like `@log_calls`.
- Ensure new code emits structured, searchable logs that match existing patterns.

## Git and CI workflow

- Never push directly to `main`. Work on feature branches: `feat/...`, `fix/...`, `chore/...`.
- Use conventional commits (`feat: …`, `fix: …`, `docs: …`, `test: …`, `ci: …`).
- Before pushing, always:
  - Run `./scripts/run_ci_locally.sh`, or
  - Run the trio: `uv run ruff check src tests`, `uv run mypy src tests`, `uv run pytest -v`.
- Treat GitHub Actions as the final arbiter of CI. Investigate and fix failing workflows before merging.
- Use the `gh` CLI to create and manage PRs when available.

## Function and file lifecycle

When you add or significantly modify a function:

- Update `docs/function_registry.md`.
- Prefer updating existing tests or extending them instead of only adding new ones.
- Ensure all call sites and references are updated if functions are moved or renamed.

When you add tasks or discover follow‑ups:

- Add or update entries in `todo.md` and mark them completed when resolved.
- Keep plans and TODOs aligned with the actual state of the code.

## Workflow steps you should follow

1. **Deconstruct**
   - Clarify the request and extract core intent, inputs, outputs, constraints, and missing information.
2. **Diagnose**
   - Check existing code, tests, and docs for similar patterns or prior art.
   - Look for reuse opportunities before adding new modules or abstractions.
3. **Develop**
   - Write small, type‑hinted units with Google‑style docstrings and logging.
   - Choose techniques appropriate to the task (e.g., numerical methods, PlotGrid recipes, storage helpers).
4. **Deliver and verify**
   - Run `uv run ruff`, `uv run mypy`, `uv run pytest`, and `./scripts/run_ci_locally.sh`.
   - Update examples and notebooks when APIs or behavior change.
   - Commit to a feature branch, push, open/update a PR, and ensure CI is green.

## Boundaries

Use the following as hard constraints on your behavior.

### Always do

- Use **UV** for all Python execution and dependency management; never call `python` or `pip` directly.
- Use **PlotGrid** and the existing plotting stack for any new plots.
- Use `StorageManager` and the provided storage helpers for all storage operations.
- Run local linting, type checks, and tests before proposing a merge.
- Prefer reusing existing code, utilities, and patterns over inventing new systems.
- Keep changes small, readable, and supported by tests.

### Ask first

- Before making **major refactors** that change public APIs, module boundaries, or core abstractions.
- Before changing **database/storage schemas**, adding new dependencies, or modifying CI/CD configuration.
- Before removing or substantially rewriting existing tests or documentation sections.
- When you have failed at an approach three times (e.g., repeated failing tests or CI), stop and request clarification or propose a simpler plan.

### Never do

- Never run `python ...`, `python -m pytest`, or `pip install ...` directly.
- Never use `--no-verify` to bypass commit hooks or comment out tests to get CI green.
- Never push directly to `main` or merge code with known failing tests.
- Never introduce new logging or storage frameworks instead of using the existing utilities.
- Never handcraft Redis keys or bypass `StorageManager`.
- Never add new direct plotting systems instead of using PlotGrid.
- Never commit secrets, API keys, or edit generated/vendor directories such as `node_modules/` or similar.

## Example of good behavior

When asked to “add a new metric and plot it”:

- Inspect existing metrics modules under `src/` and the function registry in `docs/function_registry.md` for similar patterns.
- Implement the metric in the appropriate `metrics` module with clear type hints, docstring, logging, and tests in `tests/`.
- Persist results through the storage helpers and add a PlotGrid-based visualization, reusing existing plotting helpers.
- Update relevant docs and `docs/function_registry.md`.
- Run `uv run ruff`, `uv run mypy`, `uv run pytest -v -n auto --cov`, then `./scripts/run_ci_locally.sh` before proposing changes.
