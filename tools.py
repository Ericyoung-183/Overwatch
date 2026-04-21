"""Overwatch review tools: read-only operations the reviewer can invoke."""
import os
import subprocess

from config import MAX_GIT_DIFF_CHARS


# --- Tool Definitions (Anthropic tool_use schema) ---

TOOL_DEFINITIONS = [
    {
        "name": "grep_codebase",
        "description": "Search for a pattern in the project files. Returns matching lines with file paths and line numbers. Use this to verify claims like 'all callers were updated' or 'no hardcoded secrets remain'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Subdirectory or file to search in (relative to project root). Empty = entire project."},
                "include": {"type": "string", "description": "File glob to filter (e.g. '*.py', '*.ts'). Empty = all files."},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file's contents. Use to verify code changes, check config values, or inspect files mentioned in the conversation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
                "line_start": {"type": "integer", "description": "Start line (1-based). Omit to read from beginning."},
                "line_end": {"type": "integer", "description": "End line (inclusive). Omit to read to end."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "git_diff",
        "description": "Show git changes. Use to see what actually changed vs what the conversation claims changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "Git ref to diff against (e.g. 'HEAD~3', 'main'). Default: 'HEAD' (uncommitted changes)."},
                "path": {"type": "string", "description": "Limit diff to specific file or directory. Empty = all."},
            },
            "required": [],
        },
    },
    {
        "name": "git_log",
        "description": "Show recent git commits. Use to understand what was committed and when.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of commits to show. Default: 10."},
                "path": {"type": "string", "description": "Limit to commits touching this file/dir. Empty = all."},
            },
            "required": [],
        },
    },
    {
        "name": "list_files",
        "description": "List files in a directory. Use to check if expected files exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path relative to project root. Empty = project root."},
            },
            "required": [],
        },
    },
]


# --- Tool Execution ---

MAX_TOOL_OUTPUT = 4000  # Per-tool output truncation


def _truncate(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) > limit:
        return text[:limit] + f"\n\n... [truncated at {limit} chars]"
    return text


def _run_cmd(cmd: list[str], cwd: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            output = output + "\n[stderr] " + result.stderr.strip()[:500] if output else result.stderr.strip()[:500]
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out)"
    except Exception as e:
        return f"(error: {e})"


def execute_tool(name: str, input_data: dict, project_cwd: str) -> str:
    """Execute a tool and return the result as a string."""
    if not project_cwd or not os.path.isdir(project_cwd):
        return "(error: invalid project directory)"

    if name == "grep_codebase":
        pattern = input_data.get("pattern", "")
        path = input_data.get("path", "")
        include = input_data.get("include", "")
        if not pattern:
            return "(error: pattern is required)"
        cmd = ["grep", "-rn", "--include", include, pattern] if include else ["grep", "-rn", pattern]
        search_path = os.path.join(project_cwd, path) if path else project_cwd
        cmd.append(search_path)
        return _truncate(_run_cmd(cmd, project_cwd))

    elif name == "read_file":
        file_path = input_data.get("path", "")
        if not file_path:
            return "(error: path is required)"
        full_path = os.path.join(project_cwd, file_path)
        if not os.path.isfile(full_path):
            return f"(file not found: {file_path})"
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            start = input_data.get("line_start", 1) - 1
            end = input_data.get("line_end", len(lines))
            selected = lines[max(0, start):end]
            numbered = "".join(f"{i+start+1:4d}| {line}" for i, line in enumerate(selected))
            return _truncate(numbered)
        except Exception as e:
            return f"(error reading file: {e})"

    elif name == "git_diff":
        ref = input_data.get("ref", "HEAD")
        path = input_data.get("path", "")
        cmd = ["git", "diff", ref]
        if path:
            cmd += ["--", path]
        return _truncate(_run_cmd(cmd, project_cwd), MAX_GIT_DIFF_CHARS)

    elif name == "git_log":
        count = input_data.get("count", 10)
        path = input_data.get("path", "")
        cmd = ["git", "log", f"--oneline", f"-{min(count, 50)}"]
        if path:
            cmd += ["--", path]
        return _truncate(_run_cmd(cmd, project_cwd))

    elif name == "list_files":
        path = input_data.get("path", "")
        target = os.path.join(project_cwd, path) if path else project_cwd
        if not os.path.isdir(target):
            return f"(directory not found: {path})"
        cmd = ["ls", "-la", target]
        return _truncate(_run_cmd(cmd, project_cwd))

    return f"(unknown tool: {name})"
