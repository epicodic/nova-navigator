# Coding Conventions

This project is **Python 3.12 only**.

## Python

- **Formatter / linter:** `ruff` (120-char line length, 4-space indent)
- **Type checking:** `uv run ty check .`
- All functions and methods must have **full type annotations** 
- Use `X | None` — not `Optional[X]`
- Use builtin collection types: `list`, `dict`, `set`, `tuple` — not `typing.List` etc.

### Naming

- `snake_case` — functions, variables, members
- `UpperCamelCase` — types, classes
- `_` prefix — private names
- `UPPER_CASE` — constants

### Docstrings

Google style; encouraged for public API; multiline docstrings start immediately after `"""` (no blank line).

## Testing

- Framework: **pytest**
- Test files: `test_*.py` in `tests/`
- All test functions fully type-annotated
- Run one test: `uv run pytest tests/path/test_module.py::test_name -v`
- Run all tests: `uv run pytest`
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- All QA checks: `uv run qa`
