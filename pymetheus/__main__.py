import argparse
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
from textual.widget import Widget
from textual.widgets import Header, Footer, DataTable, Label, Button, Tree, \
    Placeholder, Static, Input, Checkbox
from textual.widgets._data_table import RowKey
from textual.widgets._tree import TreeNode

from .models_pymetheus import Item
from .zotero_csl_interop import ITEM_TYPE_NAMES, FIELD_NAMES, CREATOR_TYPE_NAMES
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
        def __init__(self, _id: str | None, /):
            super().__init__()
            self.selected_collection_id = _id

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
            select name from collection
        """).fetchall()
        for col_name, in cols:
            self.root.add_leaf(col_name, data=col_name)
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
                where name = ?
            """,
            (node.data, )
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

        new = await self.app.push_screen_wait(RenameCollectionScreen(node.data))
        if new is None:
            return

        cur = self.db_connection.cursor()
        cur.execute(
            """
                update collection
                set name = ?
                where name = ?
            """,
            (new, node.data)
        )
        self.db_connection.commit()
        node.data = new
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
                (new_name, )
            ).fetchone():
                break
            counter += 1

        cur.execute(
            """
                insert into collection (name)
                values (?)
            """,
            (new_name, )
        )
        self.db_connection.commit()
        self.root.add_leaf(new_name, data=new_name)

    @work
    async def action_export_coll(self):
        pass  # TODO.


class ItemCollectionScreen(ModalScreen):
    def __init__(self, db_connection: sqlite3.Connection, rowid: int):
        super().__init__(classes="modal-screen")
        self.db_connection = db_connection
        self.rowid = rowid

    def compose(self) -> ComposeResult:
        with Widget(classes="modal-dialog"):
            yield Label("Manage collections of item", classes="question")
            with VerticalScroll(classes="checkboxes"):
                cur = self.db_connection.cursor()
                collections = cur.execute("""
                    select name from collection
                """).fetchall()

                active_collections = cur.execute(
                    """
                        select c.name
                        from collection_entry e
                        join collection c on e.collection = c.rowid
                        where e.item = ?
                    """,
                    (self.rowid, )
                ).fetchall()

                for collection_name, in collections:
                    yield Checkbox(
                        label=collection_name,
                        value=(collection_name,) in active_collections,
                        name=collection_name
                    )
            with Widget(classes="modal-buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query(Checkbox).first().focus()

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
                (self.rowid, )
            )
            for checkbox in self.query(Checkbox):
                if checkbox.value:
                    cur.execute(
                        """
                            insert into collection_entry (collection, item)
                            select rowid, ? from collection
                            where name = ?
                        """,
                        (self.rowid, checkbox.name)
                    )
            self.db_connection.commit()
            cur.close()

            self.dismiss()


class ItemsPanel(Static):
    BINDINGS = [
        Binding("ctrl+n", "new_item", "New"),
        Binding("ctrl+d", "duplicate_item", "Duplicate"),
        Binding("ctrl+s", "set_collections", "Collection..."),
        Binding("delete", "delete_item", "Delete"),
    ]

    selected_row_key: reactive[RowKey | None] = reactive(None)

    selected_collection: reactive[str | None] = reactive(None)
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

        self.watch(self, "selected_collection", self.refresh_dt)
        self.watch(self, "search_string", self.refresh_dt)

    def refresh_dt(self) -> None:
        dt = self.query_one("#items-dt", DataTable)
        dt.clear()
        cur = self.db_connection.cursor()
        if self.selected_collection is not None:
            items = cur.execute(
                """
                    select item.rowid, item.type, item.field_data, item.creators
                    from collection_entry
                    join item on collection_entry.item = item.rowid
                    where collection_entry.collection = ?
                """,
                (self.selected_collection, )
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
                    key=i_rowid,
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
            (old_rowid, )
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
            (self.selected_row_key.value, )
        )
        self.db_connection.commit()
        cur.close()
        dt = self.query_one("#items-dt", DataTable)
        print(dt.rows.keys())
        dt.remove_row(self.selected_row_key)
        dt.action_select_cursor()

    @work
    async def action_set_collections(self):
        await self.app.push_screen_wait(ItemCollectionScreen(
            db_connection=self.db_connection,
            rowid=self.selected_row_key.value,
        ))


class FieldsPanel(Static):
    BINDINGS = [
        Binding("ctrl+n", "add_creator", "Add Creator"),
        Binding("ctrl+e", "edit_field", "Edit"),
        Binding("delete", "clear_field", "Clear"),
    ]

    selected_item: reactive[int | None] = reactive(None)
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

    def watch_selected_item(self, value: int | None) -> None:
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
            (value, )
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
                key=any_field.name
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
        if self.selected_item is None:
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
            self.update_item_wo_commit(item, self.selected_item)
            self.db_connection.commit()
            fields.remove_row(self.selected_field_name)
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
            self.update_item_wo_commit(item, self.selected_item)
            self.db_connection.commit()
            creators.remove_row(f"{sel_c_type}.{sel_c_index}")
            return


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

    selected_collection: reactive[str | None] = reactive(None)
    selected_item: reactive[int | None] = reactive(None)

    @on(CollectionsPanel.Selected)
    def on_collection_selected(self, event: CollectionsPanel.Selected):
        self.selected_collection = event.selected_collection_id

    @on(ItemsPanel.Selected)
    def on_item_selected(self, event: ItemsPanel.Selected):
        if event.rowid is not None:
            self.selected_item = int(event.rowid)
        else:
            self.selected_item = None

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
            yield ItemsPanel(self.db_connection)\
                .data_bind(PymetheusApp.selected_collection)
            yield FieldsPanel(self.db_connection)\
                .data_bind(PymetheusApp.selected_item)
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
