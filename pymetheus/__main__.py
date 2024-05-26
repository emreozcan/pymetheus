import argparse
import os
import sqlite3
from pathlib import Path

from .db import create_library_at, open_library_at, get_connection_from_args
from .paths import get_app_data_dir, search_library_file_with_precedence, \
    get_default_library_path




def main() -> None:
    main_argparser = argparse.ArgumentParser()

    main_argparser.add_argument(
        "-L", "--library",
        help="Path to the library to use",
        type=Path,
        metavar="LIBRARY_PATH",
    )

    parsed_args = main_argparser.parse_args()
    connection = get_connection_from_args(parsed_args)

    print(connection)


if __name__ == "__main__":
    main()
