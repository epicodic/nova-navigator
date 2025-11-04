# from dataclasses import dataclass
# from importlib import import_module, resources
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, TextArea

from .vfs import VFSPath

# SYNTAX_HIGHLIGHTING_ENABLED = True
# if SYNTAX_HIGHLIGHTING_ENABLED:
#     from tree_sitter import Language

#     @dataclass
#     class LanguageDefinition:
#         language: Language
#         highlight_query: str

#     _LANGUAGE_CACHE: dict[str, LanguageDefinition] = {}

#     def get_language_definition(language_name: str) -> LanguageDefinition | None:
#         tree_sitter_module_name = f"tree_sitter_{language_name}"
#         highlight_query_path = resources.files(tree_sitter_module_name) / "queries/highlights.scm"

#         if not highlight_query_path.is_file():
#             log.warning(f"No 'highlights.scm' found for language {language_name!r}.")
#             return None

#         highlight_query = highlight_query_path.read_text()

#         if language_name in _LANGUAGE_CACHE:
#             return _LANGUAGE_CACHE[language_name]

#         try:
#             module = import_module(f"tree_sitter_{language_name}")
#         except ImportError:
#             return None
#         else:
#             try:
#                 if language_name == "xml":
#                     # xml uses language_xml() instead of language()
#                     # it's the only outlier amongst the languages in the `textual[syntax]` extra
#                     language = Language(module.language_xml())
#                 else:
#                     language = Language(module.language())
#             except (OSError, AttributeError):
#                 log.warning(f"Could not load language {language_name!r}.")
#                 return None
#             else:
#                 language_def = LanguageDefinition(language=language, highlight_query=highlight_query)
#                 _LANGUAGE_CACHE[language_name] = language_def
#                 return language_def


class EditorFooter(Footer):
    _cursor_position: Static | None

    DEFAULT_CSS = """
        Static.-cursor-position {
            width: auto;
            dock: right;
            padding-right: 1;
            border-left: vkey $foreground 20%;
        }
    """

    def __init__(self) -> None:
        super().__init__()
        self._cursor_position = None

    def compose(self) -> ComposeResult:
        yield from super().compose()  # renders the default keybindings
        self._cursor_position = Static("", classes="-cursor-position")
        yield self._cursor_position

    def set_sursor_position(self, line: int, column: int) -> None:
        if self._cursor_position is not None:
            self._cursor_position.update(f"[{line}, {column}]")


class Editor(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding(key="escape", action="app.pop_screen", description="Close", priority=True)]

    def compose(self) -> ComposeResult:
        self.text_area = TextArea("", id="editor", show_line_numbers=True, tab_behavior="indent", soft_wrap=False)
        # self.cursor_position = Static("[1, 1]", id="cursor-position")
        yield self.text_area
        self.footer = EditorFooter()
        yield self.footer

    def open(self, path: VFSPath) -> None:
        assert isinstance(path, VFSPath), "Only local paths are supported at the moment"
        with open(path.path) as f:
            content = f.read()

        self.text_area.text = content

        file_suffix = path.path.suffix.lower()
        LANGUAGE_MAP = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".java": "java",
            ".c": "c",
            ".h": "c",
            # ".cpp": "cpp",
            # ".cxx": "cpp",
            # ".cc": "cpp",
            # ".c++": "cpp",
            # ".hpp": "cpp",
            # ".h++": "cpp",
            # ".hxx": "cpp",
            ".html": "html",
            ".css": "css",
            ".tcss": "css",
            ".json": "json",
            ".toml": "toml",
            ".xml": "xml",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".rs": "rust",
            ".md": "markdown",
            ".sh": "bash",
            ".bash": "bash",
            ".go": "go",
        }

        language = LANGUAGE_MAP.get(file_suffix)
        # if SYNTAX_HIGHLIGHTING_ENABLED and language:
        #     language_def = get_language_definition(language)

        #     if language_def:
        #         self.text_area.register_language(language, language_def.language, language_def.highlight_query)
        #         self.text_area.language = language

        self.text_area.language = language
        self.title = f"Editing: {path.name}"

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        # log.debug(f"Selection changed: ", event.selection.end)
        self.footer.set_sursor_position(event.selection.end[0] + 1, event.selection.end[1] + 1)
