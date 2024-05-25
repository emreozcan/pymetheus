import abc
import argparse
import os
import shutil
import sys
from pathlib import Path

from .paths import get_app_data_dir, search_library_precedence


def main() -> None:
    main_argparser = argparse.ArgumentParser()

    main_argparser.add_argument(
        "-L", "--library",
        help="Path to the library to use",
        type=Path,
        metavar="LIBRARY_PATH",
    )

    parsed_args = main_argparser.parse_args()

    if parsed_args.library:
        library_path = search_library_precedence(
            [parsed_args.library],
        )
    else:
        library_path = search_library_precedence(
            [
                get_app_data_dir(),
                os.getcwd(),
            ],
        )

    if library_path is None:
        print("No library found.")
        sys.exit(1)


if __name__ == "__main__":
    main()
