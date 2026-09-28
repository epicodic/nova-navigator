# Upstream Reference

This file documents the vendored code from upstream Textual and how to manage updates.

## Upstream Source

**Project:** Textual  
**License:** MIT  
**Version:** 8.2.8  
**Repository:** https://github.com/Textualize/textual  
**Homepage:** https://textual.textualize.io/

## Vendored Files

The following files are vendored from Textual 8.2.8 with modifications:

| Vendored Path | Upstream Path | Notes |
|---|---|---|
| `widget/_text_area.py` | `src/textual/widgets/_text_area.py` | Class renamed: `TextArea` → `NovaTextArea`; imports updated to use vendored document package |
| `widget/_text_area_theme.py` | `src/textual/_text_area_theme.py` | Imports updated to reference `NovaTextArea` |
| `document/_document.py` | `src/textual/document/_document.py` | No changes |
| `document/_document_navigator.py` | `src/textual/document/_document_navigator.py` | Imports updated to use vendored document package |
| `document/_edit.py` | `src/textual/document/_edit.py` | Imports updated to reference `NovaTextArea` |
| `document/_history.py` | `src/textual/document/_history.py` | Imports updated to use vendored document package |
| `document/_syntax_aware_document.py` | `src/textual/document/_syntax_aware_document.py` | Imports updated to use vendored document package |
| `document/_wrapped_document.py` | `src/textual/document/_wrapped_document.py` | Imports updated to use vendored document package |
| `document/__init__.py` | `src/textual/document/__init__.py` | No changes |

## Why Vendored?

The TextArea widget is vendored (copied) rather than subclassed because:

1. **Anticipated heavy modifications**: Future versions will add support for very large files, extremely long lines, and custom buffer management.
2. **Minimize coupling**: By vendoring, we avoid tight coupling to Textual's internal APIs, which may change between versions.
3. **Control over performance optimizations**: We can optimize the implementation for Nova Navigator's specific use cases.
4. **Freedom from Textual version constraints**: We pin our own vendored code independently.

## Updating from Upstream

To upgrade from a newer version of Textual:

1. Check the Textual repository for the target version.
2. Compare the vendored files against the upstream version using `diff`:
   ```bash
   diff -u src/nova_editor/widget/_text_area.py \
     /path/to/textual/src/textual/widgets/_text_area.py
   ```
3. For each file, carefully merge upstream changes while preserving:
   - The `NovaTextArea` class name (instead of `TextArea`)
   - All import paths pointing to `nova_editor` packages
4. Test thoroughly using `uv run qa` and manual testing.
5. Update the version reference at the top of this file.

## License

Textual is licensed under the MIT License. See `LICENSE.textual` in this directory.
