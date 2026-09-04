"""Soulboard-specific search tools."""

import re
from collections import deque
from collections.abc import Iterable, Iterator
from io import TextIOWrapper
from typing import Any

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import (
    _DEFAULT_HEAD_LIMIT,
    GrepTool,
    _is_binary,
    _match_glob,
    _matches_type,
    _paginate,
)


class SoulGrepTool(GrepTool):
    """Grep implementation that streams files and accepts files up to 1 GiB."""

    _MAX_FILE_BYTES = 1024 * 1024 * 1024

    @property
    def description(self) -> str:
        return (
            "Search file contents with a regex pattern. "
            "Default output_mode is files_with_matches (file paths only); "
            "use content mode for matching lines with context. Prefer this "
            "over shell grep for ordinary workspace searches. "
            "Skips binary and files >1 GiB. Supports glob/type filtering."
        )

    @classmethod
    def from_upstream(cls, tool: GrepTool) -> "SoulGrepTool":
        """Preserve the filesystem policy of an already-created grep tool."""
        return cls(
            workspace=tool._workspace,
            allowed_dir=tool._allowed_dir,
            extra_allowed_dirs=tool._extra_allowed_dirs,
            file_states=tool._explicit_file_states,
            restrict_to_workspace=tool._restrict_to_workspace,
            sandbox_restricts_workspace=tool._sandbox_restricts_workspace,
        )

    @staticmethod
    def _format_streamed_block(
        display_path: str,
        match_line: int,
        lines: list[tuple[int, str]],
    ) -> str:
        block = [f"{display_path}:{match_line}"]
        for line_no, line in lines:
            marker = ">" if line_no == match_line else " "
            block.append(f"{marker} {line_no}| {line}")
        return "\n".join(block)

    def _matching_blocks(
        self,
        stream: Iterable[str],
        regex: re.Pattern[str],
        display_path: str,
        before: int,
        after: int,
    ) -> Iterator[str]:
        previous: deque[tuple[int, str]] = deque(maxlen=before)
        pending: list[tuple[int, list[tuple[int, str]], int]] = []

        for line_no, raw_line in enumerate(stream, start=1):
            line = raw_line.rstrip("\r\n")
            next_pending: list[tuple[int, list[tuple[int, str]], int]] = []
            for match_line, block_lines, remaining in pending:
                block_lines.append((line_no, line))
                remaining -= 1
                if remaining == 0:
                    yield self._format_streamed_block(
                        display_path, match_line, block_lines
                    )
                else:
                    next_pending.append((match_line, block_lines, remaining))
            pending = next_pending

            if regex.search(line):
                block_lines = [*previous, (line_no, line)]
                if after == 0:
                    yield self._format_streamed_block(
                        display_path, line_no, block_lines
                    )
                else:
                    pending.append((line_no, block_lines, after))
            previous.append((line_no, line))

        for match_line, block_lines, _remaining in pending:
            yield self._format_streamed_block(display_path, match_line, block_lines)

    async def execute(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        type: str | None = None,
        case_insensitive: bool = False,
        fixed_strings: bool = False,
        output_mode: str = "files_with_matches",
        context_before: int = 0,
        context_after: int = 0,
        max_matches: int | None = None,
        max_results: int | None = None,
        head_limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> str:
        try:
            target = self._resolve(path or ".")
            if not target.exists():
                return f"Error: Path not found: {path}"
            if not (target.is_dir() or target.is_file()):
                return f"Error: Unsupported path: {path}"

            flags = re.IGNORECASE if case_insensitive else 0
            try:
                needle = re.escape(pattern) if fixed_strings else pattern
                regex = re.compile(needle, flags)
            except re.error as exc:
                return f"Error: invalid regex pattern: {exc}"

            if head_limit is not None:
                limit = None if head_limit == 0 else head_limit
            elif output_mode == "content" and max_matches is not None:
                limit = max_matches
            elif output_mode != "content" and max_results is not None:
                limit = max_results
            else:
                limit = _DEFAULT_HEAD_LIMIT

            blocks: list[str] = []
            result_chars = 0
            seen_content_matches = 0
            truncated = False
            size_truncated = False
            skipped_binary = 0
            skipped_large = 0
            matching_files: list[str] = []
            counts: dict[str, int] = {}
            file_mtimes: dict[str, float] = {}
            root = target if target.is_dir() else target.parent

            for file_path in self._iter_files(target):
                rel_path = file_path.relative_to(root).as_posix()
                if glob and not _match_glob(rel_path, file_path.name, glob):
                    continue
                if not _matches_type(file_path.name, type):
                    continue

                stat = file_path.stat()
                if stat.st_size > self._MAX_FILE_BYTES:
                    skipped_large += 1
                    continue

                display_path = self._display_path(file_path, root)
                file_had_match = False
                blocks_before_file = len(blocks)
                chars_before_file = result_chars
                matches_before_file = seen_content_matches
                with file_path.open("rb") as raw_stream:
                    sample = raw_stream.read(4096)
                    if _is_binary(sample):
                        skipped_binary += 1
                        continue
                    raw_stream.seek(0)

                    try:
                        with TextIOWrapper(raw_stream, encoding="utf-8") as stream:
                            if output_mode == "content":
                                for block in self._matching_blocks(
                                    stream,
                                    regex,
                                    display_path,
                                    context_before,
                                    context_after,
                                ):
                                    file_had_match = True
                                    seen_content_matches += 1
                                    if seen_content_matches <= offset:
                                        continue
                                    if limit is not None and len(blocks) >= limit:
                                        truncated = True
                                        break
                                    extra_separator = 2 if blocks else 0
                                    if (
                                        result_chars + extra_separator + len(block)
                                        > self._MAX_RESULT_CHARS
                                    ):
                                        size_truncated = True
                                        break
                                    blocks.append(block)
                                    result_chars += extra_separator + len(block)
                            else:
                                count = 0
                                for line in stream:
                                    if not regex.search(line.rstrip("\r\n")):
                                        continue
                                    file_had_match = True
                                    count += 1
                                    if output_mode == "files_with_matches":
                                        break
                                if output_mode == "count" and count:
                                    counts[display_path] = count
                    except UnicodeDecodeError:
                        if output_mode == "content":
                            del blocks[blocks_before_file:]
                            result_chars = chars_before_file
                            seen_content_matches = matches_before_file
                        else:
                            counts.pop(display_path, None)
                        skipped_binary += 1
                        continue

                if file_had_match and output_mode in {"count", "files_with_matches"}:
                    matching_files.append(display_path)
                    file_mtimes[display_path] = stat.st_mtime
                if truncated or size_truncated:
                    break

            if output_mode == "files_with_matches":
                ordered_files = sorted(
                    matching_files,
                    key=lambda name: (-file_mtimes.get(name, 0.0), name),
                )
                paged, truncated = _paginate(ordered_files, limit, offset)
                result = "\n".join(paged) if paged else (
                    f"No matches found for pattern '{pattern}' in {path}"
                )
            elif output_mode == "count":
                ordered_files = sorted(
                    matching_files,
                    key=lambda name: (-file_mtimes.get(name, 0.0), name),
                )
                paged, truncated = _paginate(ordered_files, limit, offset)
                result = "\n".join(f"{name}: {counts[name]}" for name in paged)
                if not result:
                    result = f"No matches found for pattern '{pattern}' in {path}"
            else:
                result = "\n\n".join(blocks) if blocks else (
                    f"No matches found for pattern '{pattern}' in {path}"
                )

            notes: list[str] = []
            if output_mode == "content" and truncated:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif output_mode == "content" and size_truncated:
                notes.append("(output truncated due to size)")
            elif truncated and output_mode in {"count", "files_with_matches"}:
                notes.append(f"(pagination: limit={limit}, offset={offset})")
            elif (
                output_mode in {"count", "files_with_matches"} and offset > 0
            ) or (output_mode == "content" and offset > 0 and blocks):
                notes.append(f"(pagination: offset={offset})")
            if skipped_binary:
                notes.append(f"(skipped {skipped_binary} binary/unreadable files)")
            if skipped_large:
                notes.append(f"(skipped {skipped_large} large files)")
            if output_mode == "count" and counts:
                notes.append(
                    f"(total matches: {sum(counts.values())} in {len(counts)} files)"
                )
            if notes:
                result += "\n\n" + "\n".join(notes)
            return result
        except PermissionError as exc:
            return f"Error: {exc}"
        except Exception as exc:  # noqa: BLE001 - tool errors are returned to the agent
            return f"Error searching files: {exc}"


def replace_grep_tool(registry: ToolRegistry) -> None:
    """Replace an upstream grep instance while preserving its path policy."""
    tool = registry.get("grep")
    if not isinstance(tool, GrepTool):
        return
    registry.unregister("grep")
    registry.register(SoulGrepTool.from_upstream(tool))
