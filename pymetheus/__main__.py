import argparse
import datetime
import json
import sqlite3
import sys
from asyncio import sleep
from pathlib import Path
from random import randint

from textual import work, events, on
from textual.binding import Binding
from textual.containers import Horizontal, Grid, Vertical, ScrollableContainer, \
    VerticalScroll
from textual.events import Focus
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen, ModalScreen
from textual.validation import Validator, ValidationResult
from textual.widget import Widget
from textual.widgets import Header, Footer, DataTable, Label, Button, Tree, \
    Placeholder, Static, Input, Checkbox, OptionList, SelectionList
from textual.widgets._data_table import RowKey
from textual.widgets._option_list import Option
from textual.widgets._tree import TreeNode

from pymetheus.models_zotero import ItemType
from .models_pymetheus import Item, NameData
from .zotero_csl_interop import ITEM_TYPE_NAMES, FIELD_NAMES, \
    CREATOR_TYPE_NAMES, is_field_standard, is_field_date
from .db import get_connection_from_args

from textual.app import App, ComposeResult


class QuitConfirmScreen(ModalScreen):
    def __init__(self):
        super().__init__(classes="modal-screen")

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Are you sure you want to quit?", classes="question")
            yield Widget(classes="modal-content")
            with Widget(classes="modal-buttons"):
                yield Button("Quit", variant="error", id="quit")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        else:
            self.dismiss()


class RenameCollectionScreen(ModalScreen[str | None]):
    def __init__(self, initial_name: str):
        super().__init__(classes="modal-screen")

        self.initial_name = initial_name

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("New name for collection", classes="question")
            with ScrollableContainer(classes="modal-content"):
                yield Label("New name:")
                yield Input(
                    value=self.initial_name,
                    id="new-name",
                    placeholder="Collection name"
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            new_name = self.query_one("#new-name", Input).value
            self.dismiss(new_name)


class CollectionsPanel(Tree):
    BINDINGS = [
        Binding("ctrl+n", "create_coll", "Create"),
        Binding("ctrl+s", "export_coll", "Export"),
        Binding("ctrl+r", "rename_coll", "Rename"),
        Binding("delete", "delete_coll", "Delete"),
    ]

    selected_node: reactive[TreeNode | None] = reactive(None)

    class Selected(Message):
        """Sent when the selected collection changes"""

        def __init__(self, rowid: int | None, /):
            super().__init__()
            self.rowid = rowid

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        event.stop()
        self.selected_node = event.node
        self.post_message(self.Selected(event.node.data))

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        event.stop()
        self.selected_node = event.node
        self.post_message(self.Selected(event.node.data))

    @on(Tree.NodeCollapsed)
    def on_collapsed(self, event: Tree.NodeCollapsed) -> None:
        event.stop()
        if event.node == self.root:
            self.root.expand()
            self.select_node(self.root)
            self.post_message(self.Selected(self.root.data))

    def __init__(self, db_connection: sqlite3.Connection):
        super().__init__(label="My Library", data=None, id="collections-tree")
        self.db_connection = db_connection

    def on_mount(self):
        cur = self.db_connection.cursor()
        cols = cur.execute("""
            select rowid, name from collection
        """).fetchall()
        for rowid, col_name in cols:
            self.root.add_leaf(col_name, data=rowid)
        cur.close()
        self.root.expand()
        self.select_node(self.root)
        self.post_message(self.Selected(self.root.data))

    def action_delete_coll(self):
        node: TreeNode = self.selected_node
        if node.data is None:
            return

        cur = self.db_connection.cursor()
        cur.execute(
            """
                delete from collection
                where rowid = ?
            """,
            (node.data,)
        )
        self.db_connection.commit()
        cur.close()
        node.remove()
        self.action_select_cursor()

    @work
    async def action_rename_coll(self):
        node: TreeNode = self.selected_node
        if not node or not node.data:
            return

        new = await self.app.push_screen_wait(
            RenameCollectionScreen(node.label)
        )
        if new is None:
            return

        cur = self.db_connection.cursor()
        cur.execute(
            """
                update collection
                set name = ?
                where rowid = ?
            """,
            (new, node.data)
        )
        self.db_connection.commit()
        node.label = new

    @work
    async def action_create_coll(self):
        cur = self.db_connection.cursor()
        counter = 1
        while True:
            new_name = f"Collection {counter}"
            if not cur.execute(
                    """
                    select 1
                    from collection
                    where name = ?
                """,
                    (new_name,)
            ).fetchone():
                break
            counter += 1

        rowid, = cur.execute(
            """
                insert into collection (name)
                values (?)
                returning rowid
            """,
            (new_name,)
        ).fetchone()
        self.db_connection.commit()
        self.root.add_leaf(new_name, data=rowid)

    @work
    async def action_export_coll(self):
        pass  # TODO.


class ItemCollectionScreen(ModalScreen):
    def __init__(self, db_connection: sqlite3.Connection, item_rowid: int):
        super().__init__(classes="modal-screen")
        self.db_connection = db_connection
        self.item_rowid = item_rowid

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Manage collections of item", classes="question")
            with VerticalScroll(classes="checkboxes"):
                cur = self.db_connection.cursor()
                collections = cur.execute("""
                    select rowid, name from collection
                """).fetchall()

                active_collection_rowids = cur.execute(
                    """
                        select c.rowid
                        from collection_entry e
                        join collection c on e.collection = c.rowid
                        where e.item = ?
                    """,
                    (self.item_rowid,)
                ).fetchall()

                yield SelectionList(
                    *[
                        (
                            col_name,
                            col_rowid,
                            (col_rowid,) in active_collection_rowids
                        )
                        for col_rowid, col_name in collections
                    ]
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query(SelectionList).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss()
        elif event.button.id == "ok":
            cur = self.db_connection.cursor()
            cur.execute(
                """
                    delete from collection_entry
                    where item = ?
                """,
                (self.item_rowid,)
            )
            for col_rowid in self.query_one(SelectionList).selected:
                cur.execute(
                    """
                        insert into collection_entry (collection, item)
                        values (?, ?)
                    """,
                    (col_rowid, self.item_rowid)
                )
            self.db_connection.commit()
            cur.close()

            self.dismiss()


class ItemTypeSelectionScreen(ModalScreen[str | None]):
    def __init__(self):
        super().__init__(classes="modal-screen")

    selected_type: reactive[str | None] = reactive(None)

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self.selected_type = event.option.id

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.selected_type = event.option.id

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Select the type of item to create", classes="question")
            with VerticalScroll(classes="checkboxes"):
                yield OptionList(
                    *[
                        Option(prompt=human_name, id=codename)
                        for codename, human_name
                        in ITEM_TYPE_NAMES.items()
                    ]
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query(OptionList).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            self.dismiss(self.selected_type)


class ItemsPanel(Static):
    BINDINGS = [
        Binding("ctrl+n", "new_item", "New"),
        Binding("ctrl+d", "duplicate_item", "Duplicate"),
        Binding("ctrl+s", "set_collections", "Collection..."),
        Binding("delete", "delete_item", "Delete"),
    ]

    selected_row_key: reactive[RowKey | None] = reactive(None)

    selected_collection_id: reactive[int | None] = reactive(None)
    search_string: reactive[str] = reactive("")

    class Selected(Message):
        """Sent when the selected item changes"""

        def __init__(self, rowid: str, /):
            super().__init__()
            self.rowid = rowid

    def __init__(self, db_connection: sqlite3.Connection):
        super().__init__()
        self.db_connection = db_connection

    def compose(self) -> ComposeResult:
        with Widget(id="item-menu"):
            yield Input(
                placeholder="Search in all fields...", id="search-item"
            ).data_bind(value=ItemsPanel.search_string)
        yield DataTable(
            id="items-dt",
            cursor_type="row",
            zebra_stripes=True
        )

    @on(Input.Submitted)
    def on_input_submit(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "search-item":
            self.search_string = event.value

    @on(DataTable.RowSelected)
    def on_select(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.selected_row_key = event.row_key
        self.post_message(self.Selected(event.row_key.value))

    @on(DataTable.RowHighlighted)
    def on_highlight(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self.selected_row_key = event.row_key
        self.post_message(self.Selected(event.row_key.value))

    def on_mount(self) -> None:
        dt = self.query_one("#items-dt", DataTable)
        dt.add_column("Type", key="type")
        dt.add_column("Title", key="title")
        dt.add_column("Creator", key="creator")

        self.watch(self, "selected_collection_id", self.refresh_dt)
        self.watch(self, "search_string", self.refresh_dt)

    def refresh_dt(self) -> None:
        dt = self.query_one("#items-dt", DataTable)
        dt.clear()
        cur = self.db_connection.cursor()
        if self.selected_collection_id is not None:
            items = cur.execute(
                """
                    select item.rowid, item.type, item.field_data, item.creators
                    from collection_entry entry
                    join item on entry.item = item.rowid
                    where entry.collection = ?
                """,
                (self.selected_collection_id,)
            ).fetchall()
        else:
            items = cur.execute("""
                select item.rowid, item.type, item.field_data, item.creators
                from item
            """).fetchall()
        cur.close()

        if self.search_string:
            search_string = self.search_string.casefold()
        else:
            search_string = None
        for i_rowid, i_type, i_fdata, i_creators in items:
            item = Item.from_triplet(
                item_type=i_type,
                field_data=json.loads(i_fdata),
                creators=json.loads(i_creators),
            )
            if (
                    (search_string is None)
                    or item.search(search_string, casefolded=True)
            ):
                dt.add_row(
                    ITEM_TYPE_NAMES[item.type.name],
                    item.field_data.get("title", ""),
                    str(item.get_main_creator() or ""),
                    key=str(i_rowid),
                )
        if dt.rows:
            dt.move_cursor(row=0)
            dt.action_select_cursor()

    def action_duplicate_item(self):
        old_rowid = self.selected_row_key.value
        cur = self.db_connection.cursor()
        i_rowid, i_type, i_fdata, i_creators = cur.execute(
            """
                insert into item (type, field_data, creators)
                select type, field_data, creators
                from item
                where rowid = ?
                limit 1
                returning rowid, type, field_data, creators
            """,
            (old_rowid,)
        ).fetchone()
        self.db_connection.commit()
        dt = self.query_one("#items-dt", DataTable)
        item = Item.from_triplet(
            item_type=i_type,
            field_data=json.loads(i_fdata),
            creators=json.loads(i_creators),
        )
        dt.add_row(
            ITEM_TYPE_NAMES[item.type.name],
            item.field_data.get("title", ""),
            str(item.get_main_creator() or ""),
            key=str(i_rowid),
        )

    def action_delete_item(self):
        if self.selected_row_key is None:
            return

        cur = self.db_connection.cursor()
        cur.execute(
            """
                delete from item
                where rowid = ?
            """,
            (self.selected_row_key.value,)
        )
        self.db_connection.commit()
        cur.close()
        dt = self.query_one("#items-dt", DataTable)
        dt.remove_row(self.selected_row_key)
        self.selected_row_key = None
        dt.action_select_cursor()

    @work
    async def action_set_collections(self):
        await self.app.push_screen_wait(ItemCollectionScreen(
            db_connection=self.db_connection,
            item_rowid=self.selected_row_key.value,
        ))

    @work
    async def action_new_item(self):
        item_type = await self.app.push_screen_wait(
            ItemTypeSelectionScreen()
        )
        if item_type is None:
            return
        cur = self.db_connection.cursor()
        i_rowid, i_type, i_fdata, i_creators = cur.execute(
            """
                insert into item (type, field_data, creators)
                values (?, '{}', '{}')
                returning rowid, type, field_data, creators
            """,
            (item_type,)
        ).fetchone()
        self.db_connection.commit()
        dt = self.query_one("#items-dt", DataTable)
        item = Item.from_triplet(
            item_type=i_type,
            field_data=json.loads(i_fdata),
            creators=json.loads(i_creators),
        )
        dt.add_row(
            ITEM_TYPE_NAMES[item.type.name],
            item.field_data.get("title", ""),
            str(item.get_main_creator() or ""),
            key=str(i_rowid),
        )


class DateFieldEditor(ModalScreen[str | None]):
    def __init__(self, initial: str | None):
        super().__init__(classes="modal-screen")
        if initial:
            self.y, self.m, self.d = tuple(int(x) for x in initial.split("-"))
        else:
            today = datetime.date.today()
            self.y, self.m, self.d = today.year, today.month, today.day

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Edit date", classes="question")
            with ScrollableContainer(classes="modal-content"):
                yield Label("Date:")
                yield Input(
                    value=f"{self.y:04d}-{self.m:02d}-{self.d:02d}",
                    id="new-date",
                    placeholder="YYYY-MM-DD",
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-date", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            try:
                y, m, d = self.query_one("#new-date", Input).value.split("-")
                y, m, d = int(y), int(m), int(d)
            except ValueError as e:
                self.app.notify(
                    "Invalid date format. Use YYYY-MM-DD.",
                    severity="error",
                    timeout=5.0
                )
                self.dismiss(None)
                return
            try:
                datetime.date(year=y, month=m, day=d)
            except ValueError as e:
                self.app.notify(str(e), severity="error", timeout=5.0)
                self.dismiss(None)
                return
            self.dismiss(f"{y:04d}-{m:02d}-{d:02d}")


class StandardFieldEditor(ModalScreen[str | None]):
    def __init__(self, initial: str | None, field_name: str):
        super().__init__(classes="modal-screen")
        self.initial = initial
        self.field_name = field_name

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label(f"Edit {self.field_name}", classes="question")
            with ScrollableContainer(classes="modal-content"):
                yield Label(f"{self.field_name}:")
                yield Input(
                    value=self.initial,
                    id="new-value",
                    placeholder=self.field_name,
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-value", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            self.dismiss(self.query_one("#new-value", Input).value)


class CreatorTypeSelectionScreen(ModalScreen[str | None]):
    def __init__(self, item_type: ItemType):
        super().__init__(classes="modal-screen")
        self.item_type = item_type

    selected_type: reactive[str | None] = reactive(None)

    @on(OptionList.OptionHighlighted)
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self.selected_type = event.option.id

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.selected_type = event.option.id

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Select the type of contributor to create",
                        classes="question")
            yield OptionList(
                *[
                    Option(prompt=CREATOR_TYPE_NAMES[codename], id=codename)
                    for codename in self.item_type.creator_types
                ]
            )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query(OptionList).first().focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            self.dismiss(self.selected_type)


class NameEditor(ModalScreen[NameData | None]):
    def __init__(self, initial: NameData):
        super().__init__(classes="modal-screen")
        self.initial = initial

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Edit name", classes="question")
            with VerticalScroll(classes="modal-content"):
                yield Label("Family:")
                yield Input(
                    value=self.initial.family,
                    id="new-family",
                    placeholder="Family Name",
                )
                yield Label("Given:")
                yield Input(
                    value=self.initial.given,
                    id="new-given",
                    placeholder="Given Name",
                )
                yield Label("Suffix:")
                yield Input(
                    value=self.initial.suffix,
                    id="new-suffix",
                    placeholder="Suffix",
                )
                yield Label("Dropping:")
                yield Input(
                    value=self.initial.dropping_particle,
                    id="new-dropping-particle",
                    placeholder="Dropping Particle",
                )
                yield Label("Non-Dropping:")
                yield Input(
                    value=self.initial.non_dropping_particle,
                    id="new-non-dropping-particle",
                    placeholder="Non-Dropping Particle",
                )
                yield Label("Literal:")
                yield Input(
                    value=self.initial.literal,
                    id="new-literal",
                    placeholder="Literal",
                )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-family", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "ok":
            new = NameData(
                family=self.query_one("#new-family", Input).value,
                given=self.query_one("#new-given", Input).value,
                suffix=self.query_one("#new-suffix", Input).value,
                dropping_particle=(
                    self.query_one("#new-dropping-particle", Input).value
                ),
                non_dropping_particle=(
                    self.query_one("#new-non-dropping-particle", Input).value
                ),
                literal=self.query_one("#new-literal", Input).value,
            )
            self.dismiss(new)


class FieldsPanel(Static):
    BINDINGS = [
        Binding("ctrl+n", "add_creator", "Add Creator"),
        Binding("ctrl+e", "edit_field", "Edit"),
        Binding("delete", "clear_field", "Clear"),
    ]

    selected_item_rowid: reactive[int | None] = reactive(None)
    item_object: reactive[Item | None] = reactive(None)

    selected_field_name: reactive[str | None] = reactive(None)
    selected_creator: reactive[tuple[str, int] | None] = reactive(None)

    def __init__(self, db_connection: sqlite3.Connection):
        super().__init__()
        self.db_connection = db_connection

    def compose(self) -> ComposeResult:
        yield DataTable(
            id="fields-dt",
            cursor_type="row",
            zebra_stripes=True,
        )
        yield DataTable(
            id="creators-dt",
            cursor_type="row",
            zebra_stripes=True,
        )

    def on_mount(self) -> None:
        fields = self.query_one("#fields-dt", DataTable)
        fields.add_column("Field", key="field")
        fields.add_column("Value", key="value")

        creators = self.query_one("#creators-dt", DataTable)
        creators.add_column("Contribution", key="type")
        creators.add_column("Name", key="name")

    def watch_selected_item_rowid(self, value: int | None) -> None:
        field_dt = self.query_one("#fields-dt", DataTable)
        field_dt.clear()
        creator_dt = self.query_one("#creators-dt", DataTable)
        creator_dt.clear()
        if value is None:
            return
        cur = self.db_connection.cursor()
        data = cur.execute(
            """
                select type, field_data, creators
                from item
                where rowid = ?
            """,
            (value,)
        ).fetchone()
        cur.close()
        if data is None:
            return
        i_type, i_fdata, i_creators = data
        item = Item.from_triplet(
            item_type=i_type,
            field_data=json.loads(i_fdata),
            creators=json.loads(i_creators),
        )
        self.item_object = item
        field_dt.add_row(
            FIELD_NAMES["itemType"],
            ITEM_TYPE_NAMES[item.type.name],
            key="itemType",
        )
        for any_field in item.type.fields:
            field_dt.add_row(
                FIELD_NAMES[any_field.name],
                item.field_data.get(any_field.name, None),
                key=any_field.base_field
            )
        if field_dt.rows:
            field_dt.move_cursor(row=0)
            field_dt.action_select_cursor()
        for creator_type in item.type.creator_types:
            if creator_type in item.creators:
                for i, creator in enumerate(item.creators[creator_type]):
                    creator_dt.add_row(
                        CREATOR_TYPE_NAMES[creator_type],
                        str(creator),
                        key=f"{creator_type}.{i}"
                    )
        if creator_dt.rows:
            creator_dt.move_cursor(row=0)
            creator_dt.action_select_cursor()

    @on(DataTable.RowHighlighted)
    def on_dt_row_highlight(self, event: DataTable.RowHighlighted):
        event.stop()
        fields = self.query_one("#fields-dt", DataTable)
        creators = self.query_one("#creators-dt", DataTable)
        if event.data_table == fields:
            self.selected_field_name = event.row_key.value
        elif event.data_table == creators:
            creator_type, index = event.row_key.value.split(".")
            self.selected_creator = (creator_type, int(index))

    def update_item_wo_commit(self, item: Item, rowid: int) -> None:
        item_dict = item.as_dict()

        self.db_connection.execute(
            """
                update item
                set type = ?, field_data = ?, creators = ?
                where rowid = ?
            """,
            (
                item_dict["type"],
                json.dumps(item_dict["field_data"], ensure_ascii=False),
                json.dumps(item_dict["creators"], ensure_ascii=False),
                rowid,
            )
        )

    async def action_clear_field(self) -> None:
        if self.selected_item_rowid is None:
            return
        if self.item_object is None:
            return

        fields = self.query_one("#fields-dt", DataTable)
        creators = self.query_one("#creators-dt", DataTable)

        if fields.has_focus:
            if self.selected_field_name == "itemType":
                return
            item: Item = self.item_object
            if self.selected_field_name not in item.field_data:
                await fields.recompose()
                return
            del item.field_data[self.selected_field_name]
            self.update_item_wo_commit(item, self.selected_item_rowid)
            self.db_connection.commit()
            fields.update_cell(self.selected_field_name, "value", None)
            return
        elif creators.has_focus:
            item: Item = self.item_object
            sel_c_type, sel_c_index = self.selected_creator
            if sel_c_type not in item.creators or not item.creators[sel_c_type]:
                await creators.recompose()
                return
            del item.creators[sel_c_type][sel_c_index]
            if not item.creators[sel_c_type]:
                del item.creators[sel_c_type]
            self.update_item_wo_commit(item, self.selected_item_rowid)
            self.db_connection.commit()
            creators.remove_row(f"{sel_c_type}.{sel_c_index}")
            return

    @work
    async def action_edit_field(self) -> None:
        if self.selected_item_rowid is None:
            return
        if self.item_object is None:
            return

        fields = self.query_one("#fields-dt", DataTable)
        creators = self.query_one("#creators-dt", DataTable)

        if fields.has_focus:
            if self.selected_field_name == "itemType":
                return
            item: Item = self.item_object
            initial_value = item.field_data.get(self.selected_field_name, None)
            if is_field_date(self.selected_field_name):
                new_value = await self.app.push_screen_wait(
                    DateFieldEditor(initial_value)
                )
            else:
                new_value = await self.app.push_screen_wait(
                    StandardFieldEditor(
                        initial_value,
                        fields.get_cell(self.selected_field_name, "field")
                    )
                )
            if new_value is None:
                return
            item.field_data[self.selected_field_name] = new_value
            self.update_item_wo_commit(item, self.selected_item_rowid)
            self.db_connection.commit()
            fields.update_cell(
                self.selected_field_name,
                "value",
                new_value
            )
            return
        elif creators.has_focus:
            item: Item = self.item_object
            sel_c_type, sel_c_index = self.selected_creator
            if sel_c_type not in item.creators or not item.creators[sel_c_type]:
                return
            initial_value = item.creators[sel_c_type][sel_c_index]
            new_value = await self.app.push_screen_wait(
                NameEditor(
                    initial_value,
                )
            )
            if new_value is None:
                return
            if new_value.empty():
                del item.creators[sel_c_type][sel_c_index]
                if not item.creators[sel_c_type]:
                    del item.creators[sel_c_type]
                self.update_item_wo_commit(item, self.selected_item_rowid)
                self.db_connection.commit()
                creators.remove_row(f"{sel_c_type}.{sel_c_index}")
                return
            item.creators[sel_c_type][sel_c_index] = new_value
            self.update_item_wo_commit(item, self.selected_item_rowid)
            self.db_connection.commit()
            creators.update_cell(
                f"{sel_c_type}.{sel_c_index}",
                "name",
                str(new_value)
            )
            return

    @work
    async def action_add_creator(self) -> None:
        if self.selected_item_rowid is None:
            return
        if self.item_object is None:
            return

        creators = self.query_one("#creators-dt", DataTable)
        item: Item = self.item_object
        creator_type = await self.app.push_screen_wait(
            CreatorTypeSelectionScreen(item.type)
        )
        if creator_type is None:
            return
        if creator_type not in item.creators:
            item.creators[creator_type] = []
        item.creators[creator_type].append(NameData())
        self.update_item_wo_commit(item, self.selected_item_rowid)
        self.db_connection.commit()
        creators.add_row(
            CREATOR_TYPE_NAMES[creator_type],
            "",
            key=f"{creator_type}.{len(item.creators[creator_type]) - 1}"
        )


class PymetheusApp(App):
    TITLE = "pymetheus"

    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("ctrl+c", "check_quit", show=False, priority=True),
        Binding("ctrl+q", "quit", show=False, priority=True),
        Binding("ctrl+z", "suspend_process", show=False, priority=True),
        Binding(
            "f5", "recompose", "Refresh",
            show=False,
        ),
        Binding("f1", "app.focus('collections-tree')", "Collections"),
        Binding("f2", "app.focus('items-dt')", "Items"),
        Binding("f3", "app.focus('fields-dt')", "Data"),
        Binding("f4", "app.focus('creators-dt')", "Contributors"),
    ]
    ENABLE_COMMAND_PALETTE = False

    selected_collection_id: reactive[str | None] = reactive(None)
    selected_item_rowid: reactive[int | None] = reactive(None)

    @on(CollectionsPanel.Selected)
    def on_collection_selected(self, event: CollectionsPanel.Selected):
        self.selected_collection_id = event.rowid

    @on(ItemsPanel.Selected)
    def on_item_selected(self, event: ItemsPanel.Selected):
        if event.rowid is not None:
            self.selected_item_rowid = int(event.rowid)
        else:
            self.selected_item_rowid = None

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
            yield ItemsPanel(self.db_connection) \
                .data_bind(PymetheusApp.selected_collection_id)
            yield FieldsPanel(self.db_connection) \
                .data_bind(PymetheusApp.selected_item_rowid)
        yield Footer()

    def on_mount(self) -> None:
        footer = self.query_one(Footer)
        footer.upper_case_keys = True
        footer.compact = True
        footer.ctrl_to_caret = True

    async def action_recompose(self) -> None:
        await self.recompose()

    def action_check_quit(self) -> None:
        self.push_screen(QuitConfirmScreen())


app = PymetheusApp()

if __name__ == "__main__":
    app.run()
    sys.exit(app.return_code or 0)
