from pathlib import Path
import os
import sys

user_home_dir = Path.home()


# https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
# https://learn.microsoft.com/en-us/windows/deployment/usmt/usmt-recognized-environment-variables
# https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/MacOSXDirectories/MacOSXDirectories.html
def get_os_user_data_dir() -> Path:
    if os.name == "posix":
        if sys.platform.startswith("darwin"):
            return Path("~/Library/Application Support").expanduser()

        return Path(
            os.environ.get("XDG_DATA_HOME", "~/.local/share")
        ).expanduser()
    if os.name == "nt":
        return Path(os.environ["APPDATA"])

    raise RuntimeError(
        f"Can't get user cache directory on platform {os.name} {sys.platform}"
    )


def get_app_data_dir() -> Path:
    return get_os_user_data_dir() / "pymetheus"


def search_library_precedence(library_paths: list[Path], /) -> Path | None:
    for specified_path in library_paths:
        for parent in [specified_path, *specified_path.parents]:
            found_lib = search_library(parent)
            if found_lib:
                return found_lib

    return None


def search_library(path: Path, /) -> Path | None:
    path = path.expanduser().resolve()
    if path.is_dir():
        lib_path = path / "pymetheus.sqlite"
        if lib_path.exists() and lib_path.is_file():
            return lib_path
        return None

    if path.is_file() and path.name == "pymetheus.sqlite":
        return path

    return None
