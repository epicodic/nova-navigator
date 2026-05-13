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

## Textual pitfalls

### Never use `with` context managers in `compose_content()`

`Dialog.compose()` collects children by evaluating `*self.compose_content()` as a generator outside a proper compose parent context.
When a subclass uses `with SomeContainer(id="foo"):` inside `compose_content()`, Textual's context-manager `__exit__` attaches that container directly to the *screen* rather than to `#dialog_box`.
The widget becomes a full-width (screen-width) sibling of `#dialog_box`, which expands the layout bounding region and breaks `align: center middle` — the dialog no longer centers horizontally.

**Always use explicit constructor form instead:**

```python
# WRONG — attaches to screen, breaks centering
def compose_content(self) -> ComposeResult:
    with Horizontal(id="my_row"):
        yield Label("Name:")
        yield Input(id="my_input")

# CORRECT
def compose_content(self) -> ComposeResult:
    yield Horizontal(
        Label("Name:"),
        Input(id="my_input"),
        id="my_row",
    )
```

This applies to all Textual container context managers (`Horizontal`, `Vertical`, `ScrollableContainer`, etc.) used inside `compose_content()`.
