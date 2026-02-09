#!/usr/bin/env python3
"""
EU5 Log Analyzer
Parses error.log or debug.log and groups messages by type with occurrence counts.
Exports results to a markdown report file.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime


def parse_log(log_path: str, mod_filter: str = None) -> dict:
    """
    Parse EU5 log file and return message counts grouped by signature.

    Args:
        log_path: Path to log file
        mod_filter: Optional string to filter only messages from specific mod files

    Returns:
        Dict with message signatures as keys and message data as values
    """
    messages = defaultdict(lambda: {"count": 0, "locations": set(), "example": ""})

    current_message = None
    current_location = None

    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # Match error message line (Error: ...)
            error_match = re.search(r'Error:\s*(.+)', line)
            if error_match:
                current_message = f"ERROR: {error_match.group(1).strip()}"
                continue

            # Match warning message line (Warning: ...)
            warning_match = re.search(r'Warning:\s*(.+)', line)
            if warning_match:
                current_message = f"WARNING: {warning_match.group(1).strip()}"
                continue

            # Match debug/info messages with timestamp [HH:MM:SS][source]: message
            debug_match = re.search(r'\[\d+:\d+:\d+\]\[([^\]]+)\]:\s*(.+)', line)
            if debug_match:
                source = debug_match.group(1)
                msg = debug_match.group(2).strip()

                # Skip common noise
                if any(skip in msg.lower() for skip in ['loading', 'loaded', 'initializing', 'initialized']):
                    continue

                # Check for file location in the message itself
                file_match = re.search(r'(?:file|in|at)\s+["\']?([^"\':\s]+\.(txt|gui|gfx|yml))["\']?(?::(\d+))?', msg, re.IGNORECASE)
                if file_match:
                    file_loc = file_match.group(1)
                    line_num = file_match.group(3) or "?"

                    if mod_filter and mod_filter not in file_loc:
                        continue

                    signature = f"{source}: {msg}"
                    messages[signature]["count"] += 1
                    messages[signature]["locations"].add(f"{file_loc}:{line_num}")
                    if not messages[signature]["example"]:
                        messages[signature]["example"] = msg
                    continue

                # General debug message without file location
                signature = f"{source}: {msg}"
                messages[signature]["count"] += 1
                if not messages[signature]["example"]:
                    messages[signature]["example"] = msg
                continue

            # Match script location line (follows Error/Warning)
            location_match = re.search(r'Script location:\s*(.+)', line)
            if location_match and current_message:
                current_location = location_match.group(1).strip()

                # Apply mod filter if specified
                if mod_filter and mod_filter not in current_location:
                    current_message = None
                    current_location = None
                    continue

                # Create signature (message + file, ignoring line number)
                file_path = re.sub(r':\d+$', '', current_location)
                signature = f"{current_message} @ {file_path}"

                messages[signature]["count"] += 1
                messages[signature]["locations"].add(current_location)
                if not messages[signature]["example"]:
                    messages[signature]["example"] = current_location

                current_message = None
                current_location = None

    return messages


def export_report(messages: dict, output_path: str, log_path: str, mod_filter: str = None):
    """Export formatted report to markdown file."""

    sorted_messages = sorted(messages.items(), key=lambda x: x[1]["count"], reverse=True)
    total_messages = sum(e["count"] for _, e in sorted_messages)
    unique_types = len(sorted_messages)

    log_name = Path(log_path).name

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# EU5 Log Analysis: {log_name}\n\n")
        f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Source:** `{log_path}`\n\n")
        if mod_filter:
            f.write(f"**Filter:** `{mod_filter}`\n\n")
        f.write(f"**Total Messages:** {total_messages:,}\n\n")
        f.write(f"**Unique Types:** {unique_types}\n\n")
        f.write("---\n\n")

        f.write("## Summary Table\n\n")
        f.write("| # | Count | Message | File |\n")
        f.write("|---|-------|---------|------|\n")

        for i, (signature, data) in enumerate(sorted_messages, 1):
            parts = signature.split(" @ ", 1)
            msg = parts[0]
            file_path = parts[1] if len(parts) > 1 else "-"
            file_short = file_path.split("/")[-1] if "/" in file_path else file_path
            f.write(f"| {i} | {data['count']:,} | {msg} | {file_short} |\n")

        f.write("\n---\n\n")
        f.write("## Detailed Breakdown\n\n")

        for i, (signature, data) in enumerate(sorted_messages, 1):
            parts = signature.split(" @ ", 1)
            msg = parts[0]
            file_path = parts[1] if len(parts) > 1 else "-"

            f.write(f"### {i}. {msg}\n\n")
            f.write(f"**Count:** {data['count']:,}\n\n")
            if file_path != "-":
                f.write(f"**File:** `{file_path}`\n\n")

            locations = data["locations"]
            if locations and len(locations) <= 10:
                lines = sorted([loc.split(":")[-1] for loc in locations if ":" in loc], key=lambda x: int(x) if x.isdigit() else 0)
                if lines:
                    f.write(f"**Lines:** {', '.join(lines)}\n\n")
            elif locations:
                f.write(f"**Unique locations:** {len(locations)}\n\n")

            if data["example"] and data["example"] != file_path:
                f.write(f"**Example:** `{data['example']}`\n\n")

            f.write("---\n\n")


def main():
    # Default paths
    logs_dir = Path("/mnt/c/Users/mjaklitsch/Documents/Paradox Interactive/Europa Universalis V/logs")
    output_dir = Path(__file__).resolve().parent.parent

    # Check for command line args
    log_path = sys.argv[1] if len(sys.argv) > 1 else str(logs_dir / "error.log")

    # Auto-generate output name based on input log
    log_name = Path(log_path).stem
    default_output = output_dir / f"{log_name}_analysis.md"
    output_path = sys.argv[2] if len(sys.argv) > 2 else str(default_output)

    mod_filter = sys.argv[3] if len(sys.argv) > 3 else None

    messages = parse_log(log_path, mod_filter)
    export_report(messages, output_path, log_path, mod_filter)

    total = sum(e["count"] for e in messages.values())
    print(f"Analyzed {total:,} messages, {len(messages)} unique types")
    print(f"Report saved to: {output_path}")


if __name__ == "__main__":
    main()
