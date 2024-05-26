import argparse
import sqlite3
import sys
from asyncio import sleep
from pathlib import Path
from random import randint

from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Grid
from textual.reactive import reactive
from textual.screen import Screen, ModalScreen
from textual.widgets import Header, Footer, DataTable, Label, Button, Tree, \
    Placeholder

from .db import get_connection_from_args

from textual.app import App, ComposeResult


class QuitConfirmScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Are you sure you want to quit?", id="question"),
            Button("Quit", variant="error", id="quit"),
            Button("Cancel", variant="primary", id="cancel"),
            id="quit-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        else:
            self.dismiss()


class CollectionsPanel(Tree):
    def __init__(self, db_connection: sqlite3.Connection):
        super().__init__(label="My Library", data=None)
        self.db_connection = db_connection

    def on_mount(self):
        cur = self.db_connection.cursor()
        cols = cur.execute("""
            select name from collection
        """).fetchall()
        for col_name, in cols:
            self.root.add_leaf(col_name, data=col_name)
        cur.close()
        self.root.expand()


class ItemsPanel(Placeholder):
    def __init__(self):
        super().__init__()


class FieldsPanel(Placeholder):
    def __init__(self):
        super().__init__()


class PymetheusApp(App):
    TITLE = "pymetheus"

    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("ctrl+c", "check_quit", show=False, priority=True),
        Binding("ctrl+q", "quit", show=False, priority=True),
        Binding("ctrl+z", "suspend_process", show=False, priority=True),
        Binding("ctrl+p", "command_palette", "Command palette", priority=True),
        Binding("f5", "recompose", "Refresh")
    ]

    def __init__(self):
        super().__init__()

        main_argparser = argparse.ArgumentParser()

        main_argparser.add_argument(
            "-L", "--library",
            help="Path to the library to use",
            type=Path,
            metavar="LIBRARY_PATH",
        )

        parsed_args = main_argparser.parse_args()
        self.db_path, self.db_connection = get_connection_from_args(parsed_args)
        self.sub_title = self.db_path

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panel-container"):
            yield CollectionsPanel(self.db_connection)
            yield ItemsPanel()
            yield FieldsPanel()
        yield Footer()

    async def action_recompose(self) -> None:
        await self.recompose()

    def action_check_quit(self) -> None:
        self.push_screen(QuitConfirmScreen())


app = PymetheusApp()


if __name__ == "__main__":
    app.run()
    sys.exit(app.return_code or 0)
