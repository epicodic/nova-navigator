# from dataclasses import dataclass
# from importlib import import_module, resources
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Footer, Static, TextArea

from .vfs2 import VPath


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

    def open(self, path: VPath) -> None:
        assert isinstance(path, VPath), "Only local paths are supported at the moment"
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
