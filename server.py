#!/usr/bin/env python3
"""MTGA Tracker local server — finds your log automatically and serves the app."""

import http.server
import json
import os
import re
import threading
import webbrowser
from pathlib import Path

PORT = 8765
BASE_DIR = Path(__file__).parent

LOG_DIR_CANDIDATES = [
    Path.home() / ".local/share/Steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA",
    Path.home() / ".steam/steam/steamapps/compatdata/2141910/pfx/drive_c/users/steamuser/AppData/LocalLow/Wizards Of The Coast/MTGA",
    Path(os.environ.get("APPDATA", "~")) / "LocalLow/Wizards Of The Coast/MTGA",
]


def find_log_dir():
    for path in LOG_DIR_CANDIDATES:
        expanded = Path(str(path).replace("~", str(Path.home())))
        if expanded.exists():
            return expanded
    return None


def parse_segment(lines):
    """Extract the final account state from one continuous login session."""
    result = {
        "gold": None, "gems": None,
        "dailyWins": None,
        "questReward": None, "questCompleted": False,
    }
    for line in lines:
        gld = re.search(r'"Gold"\s*:\s*(\d+)', line)
        gem = re.search(r'"Gems"\s*:\s*(\d+)', line)
        if gld and gem:
            result["gold"] = int(gld.group(1))
            result["gems"] = int(gem.group(1))

        wins = re.search(r'"_dailyRewardSequenceId"\s*:\s*(\d+)', line)
        if wins:
            result["dailyWins"] = int(wins.group(1))

        if '"quests"' in line:
            quantities = re.findall(r'"quantity"\s*:\s*"(\d+)"', line)
            progresses = re.findall(r'"endingProgress"\s*:\s*(\d+)', line)
            goals = re.findall(r'"goal"\s*:\s*(\d+)', line)
            if quantities:
                reward = int(quantities[0])
                if reward in (500, 750):
                    result["questReward"] = reward
            if progresses and goals:
                result["questCompleted"] = int(progresses[0]) >= int(goals[0])

    return result if result["gold"] is not None else None


def parse_log_file(text):
    """
    Split a log file into per-account segments and return the final state of each.

    Account switch is detected when either:
      (a) SeqId drops from >1 back to 1  (played games, then new login), or
      (b) SeqId is 1 twice in a row but gems value changed (brief login, new account).
    """
    lines = text.splitlines()
    boundaries = [0]
    last_seq  = -1
    last_gems = None

    for i, line in enumerate(lines):
        if '"Gold"' not in line or '"Gems"' not in line:
            continue
        gem = re.search(r'"Gems"\s*:\s*(\d+)', line)
        seq = re.search(r'"SeqId"\s*:\s*(\d+)', line)
        if not (gem and seq):
            continue
        gems_val = int(gem.group(1))
        seq_val  = int(seq.group(1))

        new_account = (
            (seq_val == 1 and last_seq > 1) or               # case (a)
            (seq_val == 1 and last_gems is not None and gems_val != last_gems)  # case (b)
        )
        if new_account:
            boundaries.append(i)

        last_seq  = seq_val
        last_gems = gems_val

    segments = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        seg = parse_segment(lines[start:end])
        if seg:
            segments.append(seg)

    return segments


def collect_accounts(log_dir):
    """
    Read Player.log (current session) and Player-prev.log (previous session),
    parse all account segments, deduplicate by gems value keeping newest,
    and return combined list newest-first.
    """
    current_file  = log_dir / "Player.log"
    previous_file = log_dir / "Player-prev.log"

    current_segments  = []
    previous_segments = []

    if current_file.exists():
        text = current_file.read_text(encoding="utf-8", errors="replace")
        current_segments = parse_log_file(text)
        for s in current_segments:
            s["source"] = "current"

    if previous_file.exists():
        text = previous_file.read_text(encoding="utf-8", errors="replace")
        previous_segments = parse_log_file(text)
        for s in previous_segments:
            s["source"] = "previous"

    # Deduplicate: if a gems value already appears in current session,
    # drop the same-gems entry from previous session (it's stale data).
    current_gems = {s["gems"] for s in current_segments}
    unique_previous = [s for s in previous_segments if s["gems"] not in current_gems]

    # Newest first: current session segments last-to-first, then previous
    return list(reversed(current_segments)) + list(reversed(unique_previous))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/api/log":
            self._serve_log()
        else:
            super().do_GET()

    def _serve_log(self):
        log_dir = find_log_dir()
        if not log_dir:
            self._json(404, {"error": "MTGA log directory not found", "accounts": []})
            return
        try:
            accounts = collect_accounts(log_dir)
            self._json(200, {"accounts": accounts, "logDir": str(log_dir), "error": None})
        except Exception as exc:
            self._json(500, {"error": str(exc), "accounts": []})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence per-request logs


def main():
    log_dir = find_log_dir()
    if log_dir:
        print(f"Found MTGA log directory: {log_dir}")
    else:
        print("Warning: MTGA log directory not found.")

    print(f"Serving MTGA Tracker at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.\n")

    threading.Thread(
        target=lambda: (
            __import__("time").sleep(0.6),
            webbrowser.open(f"http://localhost:{PORT}"),
        ),
        daemon=True,
    ).start()

    with http.server.HTTPServer(("localhost", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
