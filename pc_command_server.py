"""
PC command endpoint for pc_agent.py. Standard library only, no installs.

Endpoints (all need  Authorization: Bearer <PC_AGENT_TOKEN>):
  POST /pc-agent/commands   body {"action": "lock|shutdown|cancel_shutdown|open_app", "app": "..."}
                            -> queues a command, returns {"id": ..., "status": "queued"}
  GET  /pc-agent/commands   the PC agent calls this: returns the next queued command or {}
  POST /pc-agent/commands   the PC agent acks with {"id": ..., "result": "..."} (no "action" key)
  GET  /pc-agent/status/<id> state and the agent's result for one command

Env vars:
  PC_AGENT_TOKEN     required (min 24 chars). Same value goes in pc_agent's config.json.
  DB_PATH            optional sqlite file (default commands.db next to this file)
  PORT               optional (default 8080)
  TLS_CERT, TLS_KEY  optional: serve HTTPS directly. Otherwise run behind an HTTPS proxy/platform.
"""

import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = "/pc-agent/commands"
STATUS_PREFIX = "/pc-agent/status/"
ALLOWED_ACTIONS = {"lock", "shutdown", "cancel_shutdown", "open_app"}
MAX_BODY = 4096

TOKEN = os.environ.get("PC_AGENT_TOKEN", "")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "commands.db"))
_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY,
            action TEXT NOT NULL,
            app TEXT,
            status TEXT NOT NULL,
            result TEXT,
            created_at REAL NOT NULL,
            dispatched_at REAL,
            done_at REAL)"""
    )
    return conn


def authorized(header_value):
    if not TOKEN or not header_value or not header_value.startswith("Bearer "):
        return False
    return hmac.compare_digest(header_value[7:].encode(), TOKEN.encode())


class Handler(BaseHTTPRequestHandler):
    server_version = "pc-command/1"

    def log_message(self, fmt, *args):  # no tokens or bodies in logs
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not authorized(self.headers.get("Authorization")):
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._authed():
            return
        if path == BASE:
            with _lock, db() as conn:
                row = conn.execute(
                    "SELECT id, action, app FROM commands WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if row is None:
                    return self._send(200, {})
                conn.execute(
                    "UPDATE commands SET status='dispatched', dispatched_at=? WHERE id=?",
                    (time.time(), row["id"]),
                )
            out = {"id": row["id"], "action": row["action"]}
            if row["app"]:
                out["app"] = row["app"]
            return self._send(200, out)
        if path.startswith(STATUS_PREFIX):
            cid = path[len(STATUS_PREFIX):]
            with _lock, db() as conn:
                row = conn.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()
            if row is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"id": row["id"], "action": row["action"], "app": row["app"],
                                    "status": row["status"], "result": row["result"]})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != BASE:
            return self._send(404, {"error": "not found"})
        if not self._authed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._send(400, {"error": "bad length"})
        if length <= 0 or length > MAX_BODY:
            return self._send(400, {"error": "body missing or too large"})
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"error": "invalid json"})
        if not isinstance(data, dict):
            return self._send(400, {"error": "expected an object"})

        if "action" in data:  # queue a new command
            action = data.get("action")
            if action not in ALLOWED_ACTIONS:
                return self._send(400, {"error": "action must be one of " + ", ".join(sorted(ALLOWED_ACTIONS))})
            app = None
            if action == "open_app":
                app = str(data.get("app", "")).strip()
                if not app or len(app) > 64:
                    return self._send(400, {"error": "open_app needs an app name (max 64 chars)"})
            cid = secrets.token_hex(8)
            with _lock, db() as conn:
                conn.execute(
                    "INSERT INTO commands (id, action, app, status, created_at) VALUES (?,?,?,?,?)",
                    (cid, action, app, "queued", time.time()),
                )
            return self._send(201, {"id": cid, "status": "queued"})

        if "result" in data and "id" in data:  # ack from the PC agent
            cid = str(data["id"])
            result = str(data["result"])[:500]
            with _lock, db() as conn:
                cur = conn.execute(
                    "UPDATE commands SET status='done', result=?, done_at=? WHERE id=? AND status!='done'",
                    (result, time.time(), cid),
                )
                if cur.rowcount == 0 and not conn.execute("SELECT 1 FROM commands WHERE id=?", (cid,)).fetchone():
                    return self._send(404, {"error": "unknown id"})
            return self._send(200, {"ok": True})

        self._send(400, {"error": "send action (to queue) or id+result (ack)"})


def main():
    if not TOKEN:
        raise SystemExit("set PC_AGENT_TOKEN in the environment first")
    if len(TOKEN) < 24:
        raise SystemExit("PC_AGENT_TOKEN must be at least 24 characters")
    db().close()
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    cert, key = os.environ.get("TLS_CERT"), os.environ.get("TLS_KEY")
    if cert and key:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"listening on :{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
