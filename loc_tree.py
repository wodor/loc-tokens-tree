#!/usr/bin/env python3
"""Count LOC and token estimates per directory with tree and ncdu-like views."""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".idea",
    ".cursor",
    ".local-dev",
    ".ruff_cache",
    "node_modules",
    "vendor",
    "var",
    "ext",
    "venv",
    "GraphQL2/Schema",
    "wp-content",
    "swagger-ui",
}

DEFAULT_EXCLUDED_PATHS = (r".*jquery.*", r".*min\.js$", r".*android.js$")
DEFAULT_EXCLUDED_PATHS_CSV = ", ".join(DEFAULT_EXCLUDED_PATHS)

DEFAULT_CODE_EXTENSIONS = (
    ".php",
    ".py",
    ".js",
    ".sh",
    ".twig",
    #    ".sql",
    ".phtml",
    ".tf",
    ".yaml",
    ".yml",
    ".cpp",
)
DEFAULT_CODE_EXTENSIONS_CSV = ",".join(DEFAULT_CODE_EXTENSIONS)
SORT_MODES = ("tokens", "lines", "size", "name")


@dataclass
class Metrics:
    lines: int = 0
    tokens: int = 0
    size_bytes: int = 0

    def add(self, other: "Metrics") -> None:
        self.lines += other.lines
        self.tokens += other.tokens
        self.size_bytes += other.size_bytes


@dataclass
class FileStats:
    path: Path
    counted: bool
    metrics: Metrics = field(default_factory=Metrics)


@dataclass
class DirectoryStats:
    path: Path
    root_metrics: Metrics = field(default_factory=Metrics)
    children_metrics: Metrics = field(default_factory=Metrics)
    total_metrics: Metrics = field(default_factory=Metrics)
    children: list["DirectoryStats"] = field(default_factory=list)
    files: list[FileStats] = field(default_factory=list)


@dataclass
class BrowserEntry:
    kind: str
    name: str
    directory: DirectoryStats | None = None
    file: FileStats | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Count lines of code and estimated tokens per directory, "
            "then show a report in tree or ncdu-like mode."
        )
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Directory to scan (defaults to current directory).",
    )
    parser.add_argument(
        "--mode",
        choices=("ncdu", "tree"),
        default="ncdu",
        help="Output mode: ncdu-like interactive browser or static tree (default: ncdu).",
    )
    parser.add_argument(
        "--chars-per-token",
        type=float,
        default=4.0,
        help="Token estimation ratio (default: 4 chars per token).",
    )
    parser.add_argument(
        "--include-blank-lines",
        action="store_true",
        help="Count blank lines as code lines.",
    )
    parser.add_argument(
        "--all-dirs",
        action="store_true",
        help="Show directories even when they have 0 lines and 0 tokens.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Directory name/path to exclude. Can be passed multiple times.",
    )
    parser.add_argument(
        "--exclude-path-regex",
        action="append",
        default=[],
        help=(
            "Regex to exclude file/dir by relative path or name. "
            "Repeatable; adds to defaults. "
            f"Defaults: {DEFAULT_EXCLUDED_PATHS_CSV}."
        ),
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=DEFAULT_CODE_EXTENSIONS_CSV,
        help=(
            "Comma-separated file extensions to treat as code. "
            f"Default: {DEFAULT_CODE_EXTENSIONS_CSV}."
        ),
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files/directories (dot-prefixed).",
    )
    return parser.parse_args()


def normalize_extensions(raw_extensions: str) -> set[str]:
    values: set[str] = set()
    for raw_item in raw_extensions.split(","):
        item = raw_item.strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = f".{item}"
        values.add(item)
    return values


def compile_exclude_path_patterns(raw_patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in raw_patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            raise SystemExit(f"Invalid --exclude-path-regex '{pattern}': {exc}")
    return compiled


def matches_exclude_path_regex(
    path: Path,
    root_path: Path,
    exclude_path_patterns: list[re.Pattern[str]],
) -> bool:
    if not exclude_path_patterns:
        return False
    rel_path = path.relative_to(root_path).as_posix()
    for pattern in exclude_path_patterns:
        if pattern.search(rel_path) or pattern.search(path.name):
            return True
    return False


def should_skip_directory(
    path: Path,
    root_path: Path,
    excluded_dirs: set[str],
    exclude_path_patterns: list[re.Pattern[str]],
    include_hidden: bool,
) -> bool:
    if not include_hidden and path.name.startswith("."):
        return True
    if matches_exclude_path_regex(path, root_path, exclude_path_patterns):
        return True

    rel_path = path.relative_to(root_path).as_posix()
    for item in excluded_dirs:
        needle = item.strip().strip("/").replace("\\", "/")
        if not needle:
            continue
        if "/" in needle:
            if rel_path == needle or rel_path.endswith(f"/{needle}"):
                return True
        elif path.name == needle:
            return True
    return False


def should_list_file(
    path: Path,
    root_path: Path,
    exclude_path_patterns: list[re.Pattern[str]],
    include_hidden: bool,
) -> bool:
    if not include_hidden and path.name.startswith("."):
        return False
    if matches_exclude_path_regex(path, root_path, exclude_path_patterns):
        return False
    return True


def is_counted_code_file(path: Path, code_extensions: set[str]) -> bool:
    return path.suffix.lower() in code_extensions


def count_file_metrics(
    path: Path,
    chars_per_token: float,
    include_blank_lines: bool,
    file_size_bytes: int,
) -> Metrics:
    if chars_per_token <= 0:
        raise ValueError("--chars-per-token must be greater than 0.")

    lines = 0
    char_count = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as file_handle:
            for line in file_handle:
                char_count += len(line)
                if include_blank_lines or line.strip():
                    lines += 1
    except (OSError, UnicodeError):
        return Metrics(size_bytes=file_size_bytes)

    tokens = int(math.ceil(char_count / chars_per_token)) if char_count else 0
    return Metrics(lines=lines, tokens=tokens, size_bytes=file_size_bytes)


def scan_directory(
    path: Path,
    root_path: Path,
    excluded_dirs: set[str],
    exclude_path_patterns: list[re.Pattern[str]],
    code_extensions: set[str],
    chars_per_token: float,
    include_blank_lines: bool,
    include_hidden: bool,
    show_all_dirs: bool,
) -> DirectoryStats | None:
    root_metrics = Metrics()
    child_nodes: list[DirectoryStats] = []
    file_nodes: list[FileStats] = []
    subdirs: list[Path] = []

    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                entry_path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if should_skip_directory(
                        path=entry_path,
                        root_path=root_path,
                        excluded_dirs=excluded_dirs,
                        exclude_path_patterns=exclude_path_patterns,
                        include_hidden=include_hidden,
                    ):
                        continue
                    subdirs.append(entry_path)
                elif entry.is_file(follow_symlinks=False):
                    if not should_list_file(
                        path=entry_path,
                        root_path=root_path,
                        exclude_path_patterns=exclude_path_patterns,
                        include_hidden=include_hidden,
                    ):
                        continue
                    file_size_bytes = 0
                    try:
                        file_size_bytes = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                    counted = is_counted_code_file(
                        entry_path,
                        code_extensions=code_extensions,
                    )
                    metrics = (
                        count_file_metrics(
                            entry_path,
                            chars_per_token=chars_per_token,
                            include_blank_lines=include_blank_lines,
                            file_size_bytes=file_size_bytes,
                        )
                        if counted
                        else Metrics(size_bytes=file_size_bytes)
                    )
                    root_metrics.add(metrics)
                    file_nodes.append(
                        FileStats(
                            path=entry_path,
                            counted=counted,
                            metrics=metrics,
                        )
                    )
    except OSError:
        return None

    for subdir in sorted(subdirs, key=lambda value: value.name.lower()):
        child = scan_directory(
            path=subdir,
            root_path=root_path,
            excluded_dirs=excluded_dirs,
            exclude_path_patterns=exclude_path_patterns,
            code_extensions=code_extensions,
            chars_per_token=chars_per_token,
            include_blank_lines=include_blank_lines,
            include_hidden=include_hidden,
            show_all_dirs=show_all_dirs,
        )
        if child is not None:
            child_nodes.append(child)

    children_metrics = Metrics()
    for child in child_nodes:
        children_metrics.add(child.total_metrics)

    total_metrics = Metrics(
        lines=root_metrics.lines + children_metrics.lines,
        tokens=root_metrics.tokens + children_metrics.tokens,
        size_bytes=root_metrics.size_bytes + children_metrics.size_bytes,
    )

    if (
        not show_all_dirs
        and total_metrics.lines == 0
        and total_metrics.tokens == 0
        and not child_nodes
        and not file_nodes
    ):
        return None

    return DirectoryStats(
        path=path,
        root_metrics=root_metrics,
        children_metrics=children_metrics,
        total_metrics=total_metrics,
        children=child_nodes,
        files=sorted(file_nodes, key=lambda value: value.path.name.lower()),
    )


def format_metrics(metrics: Metrics) -> str:
    return (
        f"{metrics.lines:,} lines, ~{metrics.tokens:,} tokens, "
        f"{format_size_bytes(metrics.size_bytes)}"
    )


def format_cell(value: int) -> str:
    return f"{value:,}"


def format_size_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"

    size = float(size_bytes)
    units = ("KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}"
    return f"{size_bytes} B"


def format_size_cell(size_bytes: int) -> str:
    return format_size_bytes(size_bytes)


def render_scaled_bar(value: int, max_value: int, bar_width: int) -> str:
    if bar_width <= 0:
        return "[]"
    if max_value <= 0 or value <= 0:
        return "[" + (" " * bar_width) + "]"

    filled = int(round((value / max_value) * bar_width))
    if filled <= 0:
        filled = 1
    if filled > bar_width:
        filled = bar_width
    return "[" + ("#" * filled) + (" " * (bar_width - filled)) + "]"


def render_loc_bar(lines: int, max_lines: int, bar_width: int) -> str:
    return render_scaled_bar(lines, max_lines, bar_width)


def render_token_bar(tokens: int, max_tokens: int, bar_width: int) -> str:
    return render_scaled_bar(tokens, max_tokens, bar_width)


def ellipsize(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return f"{text[: width - 3]}..."


def format_file_description(file_stats: FileStats) -> str:
    if file_stats.counted:
        return format_metrics(file_stats.metrics)
    suffix = file_stats.path.suffix.lower() or "<no-ext>"
    return f"{format_metrics(file_stats.metrics)}; excluded extension ({suffix})"


def render_tree(node: DirectoryStats) -> str:
    lines: list[str] = []

    def walk(
        current: DirectoryStats,
        prefix: str,
        is_last: bool,
        is_root: bool,
    ) -> None:
        branch = ""
        name = "."
        if not is_root:
            name = current.path.name
            branch = "└── " if is_last else "├── "

        lines.append(
            (
                f"{prefix}{branch}{name} "
                f"(root: {format_metrics(current.root_metrics)}; "
                f"subdirs: {format_metrics(current.children_metrics)}; "
                f"total: {format_metrics(current.total_metrics)})"
            )
        )

        child_prefix = prefix + (
            "    " if is_last and not is_root else "│   " if not is_root else ""
        )

        entries: list[tuple[str, DirectoryStats | FileStats]] = []
        for child in current.children:
            entries.append(("dir", child))
        for file_stats in current.files:
            entries.append(("file", file_stats))

        for index, (kind, obj) in enumerate(entries):
            child_is_last = index == len(entries) - 1
            branch = "└── " if child_is_last else "├── "
            if kind == "dir":
                walk(
                    obj,  # type: ignore[arg-type]
                    child_prefix,
                    child_is_last,
                    is_root=False,
                )
            else:
                file_stats = obj  # type: ignore[assignment]
                lines.append(
                    (
                        f"{child_prefix}{branch}{file_stats.path.name} "
                        f"({format_file_description(file_stats)})"
                    )
                )

    walk(node, prefix="", is_last=True, is_root=True)
    return "\n".join(lines)


def entry_metrics(entry: BrowserEntry) -> Metrics:
    if entry.kind == "dir" and entry.directory is not None:
        return entry.directory.total_metrics
    if entry.kind == "file" and entry.file is not None:
        return entry.file.metrics
    return Metrics()


def sorted_browser_entries(
    directories: list[DirectoryStats],
    files: list[FileStats],
    sort_mode: str,
) -> list[BrowserEntry]:
    entries: list[BrowserEntry] = []
    for child in directories:
        entries.append(BrowserEntry(kind="dir", name=child.path.name, directory=child))
    for file_stats in files:
        entries.append(
            BrowserEntry(kind="file", name=file_stats.path.name, file=file_stats)
        )

    if sort_mode == "name":
        return sorted(
            entries,
            key=lambda entry: (0 if entry.kind == "dir" else 1, entry.name.lower()),
        )

    def sort_key(entry: BrowserEntry) -> tuple[int, int, str]:
        metrics = entry_metrics(entry)
        if sort_mode == "lines":
            primary = metrics.lines
        elif sort_mode == "size":
            primary = metrics.size_bytes
        else:
            primary = metrics.tokens
        return (-primary, 0 if entry.kind == "dir" else 1, entry.name.lower())

    return sorted(entries, key=sort_key)


def run_ncdu(node: DirectoryStats) -> int:
    try:
        import curses
    except ImportError as exc:
        raise SystemExit(f"Failed to load curses module: {exc}")

    def draw(stdscr: "curses.window") -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)

        current = node
        history: list[tuple[DirectoryStats, int, int]] = []
        selected = 0
        scroll_offset = 0
        sort_mode = "tokens"

        while True:
            entries: list[BrowserEntry] = []
            if history:
                entries.append(BrowserEntry(kind="parent", name=".."))

            entries.extend(
                sorted_browser_entries(
                    directories=current.children,
                    files=current.files,
                    sort_mode=sort_mode,
                )
            )

            if not entries:
                entries.append(BrowserEntry(kind="placeholder", name="<empty>"))

            selected = min(max(0, selected), len(entries) - 1)

            stdscr.erase()
            height, width = stdscr.getmaxyx()
            if height < 8 or width < 60:
                stdscr.addnstr(0, 0, "Terminal too small. Resize and retry.", width - 1)
                stdscr.addnstr(1, 0, "Press q to quit.", width - 1)
                stdscr.refresh()
                key = stdscr.getch()
                if key in (ord("q"), ord("Q")):
                    return
                continue

            title = f"loc_tree ncdu | path: {current.path}"
            summary = (
                f"total {format_metrics(current.total_metrics)} | "
                f"root {format_metrics(current.root_metrics)} | "
                f"subdirs {format_metrics(current.children_metrics)}"
            )
            controls = (
                "q quit | Enter/right open dir | left/backspace up | "
                "j/k or arrows move | s sort"
            )
            sort_line = f"sort: {sort_mode}"

            stdscr.addnstr(0, 0, ellipsize(title, width - 1), width - 1, curses.A_BOLD)
            stdscr.addnstr(1, 0, ellipsize(summary, width - 1), width - 1)
            stdscr.addnstr(2, 0, ellipsize(controls, width - 1), width - 1)
            stdscr.addnstr(3, 0, ellipsize(sort_line, width - 1), width - 1)

            data_entries = [entry for entry in entries if entry.kind in ("dir", "file")]
            max_lines = max(
                (entry_metrics(entry).lines for entry in data_entries),
                default=0,
            )
            max_tokens = max(
                (entry_metrics(entry).tokens for entry in data_entries),
                default=0,
            )
            bar_width = max(6, min(16, width // 10))
            header = (
                "Name                             Type  Counted"
                "        Lines       Tokens        Size LOC Bar          Token Bar"
            )
            stdscr.addnstr(
                5,
                0,
                ellipsize(header, width - 1),
                width - 1,
                curses.A_UNDERLINE,
            )

            first_data_row = 6
            available_rows = max(1, height - first_data_row)
            if selected < scroll_offset:
                scroll_offset = selected
            if selected >= scroll_offset + available_rows:
                scroll_offset = selected - available_rows + 1

            visible_entries = entries[scroll_offset : scroll_offset + available_rows]
            name_width = max(12, width - (57 + ((bar_width + 2) * 2) + 2))

            for idx, entry in enumerate(visible_entries):
                real_idx = scroll_offset + idx
                attr = curses.A_REVERSE if real_idx == selected else 0

                if entry.kind == "parent":
                    row = (
                        f"{entry.name:<{name_width}} {'dir':<5} {'-':<7} "
                        f"{'':>12} {'':>12} {'':>11} "
                        f"{'':>{bar_width + 2}} {'':>{bar_width + 2}}"
                    )
                elif entry.kind == "placeholder":
                    loc_bar = render_loc_bar(
                        0, max_lines=max_lines, bar_width=bar_width
                    )
                    token_bar = render_token_bar(
                        0,
                        max_tokens=max_tokens,
                        bar_width=bar_width,
                    )
                    row = (
                        f"{entry.name:<{name_width}} {'-':<5} {'-':<7} "
                        f"{0:>12} {0:>12} {format_size_cell(0):>11} "
                        f"{loc_bar} {token_bar}"
                    )
                elif entry.kind == "dir" and entry.directory is not None:
                    loc_bar = render_loc_bar(
                        entry.directory.total_metrics.lines,
                        max_lines=max_lines,
                        bar_width=bar_width,
                    )
                    token_bar = render_token_bar(
                        entry.directory.total_metrics.tokens,
                        max_tokens=max_tokens,
                        bar_width=bar_width,
                    )
                    row = (
                        f"{entry.name:<{name_width}} "
                        f"{'dir':<5} {'-':<7} "
                        f"{format_cell(entry.directory.total_metrics.lines):>12} "
                        f"{format_cell(entry.directory.total_metrics.tokens):>12} "
                        f"{format_size_cell(entry.directory.total_metrics.size_bytes):>11} "
                        f"{loc_bar} {token_bar}"
                    )
                elif entry.kind == "file" and entry.file is not None:
                    loc_bar = render_loc_bar(
                        entry.file.metrics.lines,
                        max_lines=max_lines,
                        bar_width=bar_width,
                    )
                    token_bar = render_token_bar(
                        entry.file.metrics.tokens,
                        max_tokens=max_tokens,
                        bar_width=bar_width,
                    )
                    row = (
                        f"{entry.name:<{name_width}} "
                        f"{'file':<5} "
                        f"{('yes' if entry.file.counted else 'no'):<7} "
                        f"{format_cell(entry.file.metrics.lines):>12} "
                        f"{format_cell(entry.file.metrics.tokens):>12} "
                        f"{format_size_cell(entry.file.metrics.size_bytes):>11} "
                        f"{loc_bar} {token_bar}"
                    )
                else:
                    row = (
                        f"{entry.name:<{name_width}} {'?':<5} {'-':<7} "
                        f"{'':>12} {'':>12} {'':>11} "
                        f"{'':>{bar_width + 2}} {'':>{bar_width + 2}}"
                    )

                stdscr.addnstr(
                    first_data_row + idx,
                    0,
                    ellipsize(row, width - 1),
                    width - 1,
                    attr,
                )

            stdscr.refresh()
            key = stdscr.getch()

            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                selected = max(0, selected - 1)
                continue
            if key in (curses.KEY_DOWN, ord("j"), ord("J")):
                selected = min(len(entries) - 1, selected + 1)
                continue
            if key in (curses.KEY_PPAGE,):
                selected = max(0, selected - available_rows)
                continue
            if key in (curses.KEY_NPAGE,):
                selected = min(len(entries) - 1, selected + available_rows)
                continue
            if key in (curses.KEY_HOME,):
                selected = 0
                continue
            if key in (curses.KEY_END,):
                selected = len(entries) - 1
                continue
            if key in (ord("s"), ord("S")):
                mode_index = SORT_MODES.index(sort_mode)
                sort_mode = SORT_MODES[(mode_index + 1) % len(SORT_MODES)]
                selected = 0
                scroll_offset = 0
                continue

            open_keys = (curses.KEY_RIGHT, curses.KEY_ENTER, 10, 13, ord("l"), ord("L"))
            up_keys = (
                curses.KEY_LEFT,
                curses.KEY_BACKSPACE,
                127,
                8,
                ord("h"),
                ord("H"),
            )

            if key in open_keys:
                chosen = entries[selected]
                if chosen.kind == "dir" and chosen.directory is not None:
                    history.append((current, selected, scroll_offset))
                    current = chosen.directory
                    selected = 0
                    scroll_offset = 0
                elif chosen.kind == "parent" and history:
                    current, selected, scroll_offset = history.pop()
                continue

            if key in up_keys and history:
                current, selected, scroll_offset = history.pop()

    try:
        curses.wrapper(draw)
    except curses.error as exc:
        print(
            f"ncdu mode unavailable ({exc}). Falling back to tree output.",
            file=sys.stderr,
        )
        print(render_tree(node))
    return 0


def main() -> int:
    args = parse_args()
    root_path = Path(args.root).resolve()
    if not root_path.is_dir():
        raise SystemExit(f"Not a directory: {root_path}")

    code_extensions = normalize_extensions(args.extensions)
    if not code_extensions:
        code_extensions = set(DEFAULT_CODE_EXTENSIONS)

    excluded_dirs = set(DEFAULT_EXCLUDED_DIRS)
    excluded_dirs.update(name.strip() for name in args.exclude_dir if name.strip())
    exclude_path_patterns = compile_exclude_path_patterns(
        list(DEFAULT_EXCLUDED_PATHS) + list(args.exclude_path_regex)
    )

    report = scan_directory(
        path=root_path,
        root_path=root_path,
        excluded_dirs=excluded_dirs,
        exclude_path_patterns=exclude_path_patterns,
        code_extensions=code_extensions,
        chars_per_token=args.chars_per_token,
        include_blank_lines=args.include_blank_lines,
        include_hidden=args.include_hidden,
        show_all_dirs=args.all_dirs,
    )
    if report is None:
        report = DirectoryStats(path=root_path)

    if args.mode == "tree" or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(render_tree(report))
        return 0

    print("Scanning complete. Opening ncdu view...", file=sys.stderr)
    return run_ncdu(report)


if __name__ == "__main__":
    raise SystemExit(main())
