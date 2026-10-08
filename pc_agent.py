"""
PC remote agent (Windows). Runs on YOUR PC and polls a command endpoint with your token.
No inbound ports are opened: the agent only makes outgoing HTTPS requests.

Commands (anything else is refused):
  lock            -> locks the workstation
  shutdown        -> powers off after a fixed 30 second delay
  cancel_shutdown -> aborts a pending shutdown
  open_app        -> opens an app, but ONLY if its name is in apps.json

Run:  python pc_agent.py              (live)
      python pc_agent.py --dry-run    (logs what it would do, does nothing)
      python pc_agent.py --once       (one poll, then exit)
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
APPS_PATH = HERE / "apps.json"
LOG_PATH = HERE / "pc_agent.log"

SHUTDOWN_DELAY_SECONDS = 30  # fixed in code; the server cannot change it

log = logging.getLogger("pc_agent")


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not str(cfg.get("server_url", "")).startswith("https://"):
        raise ValueError("server_url must start with https://")
    if not cfg.get("token") or str(cfg["token"]).startswith("PUT_"):
        raise ValueError("set a real token in config.json")
    cfg.setdefault("poll_seconds", 5)
    return cfg


def load_apps(path):
    """Returns {app_name_lowercase: exe_path}. Only these names can ever be opened."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {name.strip().lower(): exe for name, exe in raw.items()
            if isinstance(name, str) and isinstance(exe, str)}


def do_lock(dry_run):
    if dry_run:
        return "[dry-run] would lock workstation"
    subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
    return "locked"


def do_shutdown(dry_run):
    args = ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY_SECONDS)]
    if dry_run:
        return "[dry-run] would run: " + " ".join(args)
    subprocess.run(args, check=False)
    return f"shutdown scheduled in {SHUTDOWN_DELAY_SECONDS}s"


def do_cancel_shutdown(dry_run):
    if dry_run:
        return "[dry-run] would run: shutdown /a"
    subprocess.run(["shutdown", "/a"], check=False)
    return "pending shutdown cancelled (if one was pending)"


def do_open_app(name, apps, dry_run):
    key = (name or "").strip().lower()
    if key not in apps:
        return f"refused: '{name}' is not on the whitelist"
    exe = apps[key]
    if not os.path.isfile(exe):
        return f"refused: path for '{name}' does not exist: {exe}"
    if dry_run:
        return f"[dry-run] would open {exe}"
    os.startfile(exe)  # opens the exe directly, no shell, no extra arguments
    return f"opened {name}"


def handle(command, apps, dry_run):
    action = command.get("action")
    if action == "lock":
        return do_lock(dry_run)
    if action == "shutdown":
        return do_shutdown(dry_run)
    if action == "cancel_shutdown":
        return do_cancel_shutdown(dry_run)
    if action == "open_app":
        return do_open_app(command.get("app", ""), apps, dry_run)
    return f"ignored unknown action: {action!r}"


def fetch_next_command(cfg):
    """GET the pending command. Endpoint returns {} or {"id": ..., "action": ..., "app": ...}."""
    req = urllib.request.Request(
        cfg["server_url"],
        headers={"Authorization": f"Bearer {cfg['token']}", "User-Agent": "pc-agent/1"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read(4096)
    data = json.loads(body.decode("utf-8") or "{}")
    return data if data.get("id") else None


def ack_command(cfg, command_id, result):
    """Tell the server the command ran so it is not repeated."""
    payload = json.dumps({"id": command_id, "result": result[:500]}).encode("utf-8")
    req = urllib.request.Request(
        cfg["server_url"], data=payload, method="POST",
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Content-Type": "application/json", "User-Agent": "pc-agent/1"},
    )
    with urllib.request.urlopen(req, timeout=15):
        pass


def run_loop(cfg, apps, dry_run):
    log.info("pc_agent started (dry_run=%s, apps=%s)", dry_run, sorted(apps))
    while True:
        try:
            command = fetch_next_command(cfg)
            if command:
                result = handle(command, apps, dry_run)
                log.info("command %s -> %s", command.get("action"), result)
                if not dry_run:
                    ack_command(cfg, str(command["id"]), result)
        except urllib.error.URLError as e:
            log.warning("network error: %s", e)
        except Exception as e:  # keep the agent alive
            log.exception("unexpected error: %s", e)
        time.sleep(cfg["poll_seconds"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )
    cfg = load_config(CONFIG_PATH)
    apps = load_apps(APPS_PATH)
    if args.once:
        command = fetch_next_command(cfg)
        print(handle(command, apps, args.dry_run) if command else "no pending command")
        return 0
    run_loop(cfg, apps, args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
