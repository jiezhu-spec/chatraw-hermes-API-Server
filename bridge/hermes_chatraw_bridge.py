#!/usr/bin/env python3
"""OpenAI-ish bridge from ChatRaw to Hermes' structured TUI gateway.

This service intentionally uses ``python -m tui_gateway.entry`` instead of
scraping the interactive CLI screen. The gateway emits typed events for text,
reasoning, tools, approvals, and completion; ChatRaw consumes those through the
``/v1/runs`` endpoints below.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


HOST = os.environ.get("HERMES_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_BRIDGE_PORT", "51113"))
MODEL_ID = os.environ.get("HERMES_BRIDGE_MODEL", "hermes-cli")
WORKDIR = os.environ.get("HERMES_BRIDGE_WORKDIR", "/home/rm01")
DB_PATH = os.environ.get("HERMES_BRIDGE_DB", "/home/rm01/apps/hermes-chatraw-bridge/sessions.db")
HERMES_AGENT_ROOT = os.environ.get("HERMES_AGENT_ROOT", "/home/rm01/.hermes/hermes-agent")
HERMES_STATE_DB = os.environ.get("HERMES_STATE_DB", "/home/rm01/.hermes/state.db")
HERMES_PYTHON = os.environ.get(
    "HERMES_PYTHON",
    str(Path(HERMES_AGENT_ROOT) / "venv" / "bin" / "python3"),
)
READY_TIMEOUT_SECONDS = float(os.environ.get("HERMES_GATEWAY_READY_TIMEOUT", "30"))
RPC_TIMEOUT_SECONDS = float(os.environ.get("HERMES_GATEWAY_RPC_TIMEOUT", "120"))
TURN_TIMEOUT_SECONDS = float(os.environ.get("HERMES_GATEWAY_TURN_TIMEOUT", str(60 * 60)))
RUN_TTL_SECONDS = float(os.environ.get("HERMES_BRIDGE_RUN_TTL", "1800"))
IDLE_GATEWAY_SECONDS = float(os.environ.get("HERMES_BRIDGE_GATEWAY_TTL", "900"))
DEFAULT_NONINTERACTIVE_INSTRUCTION = (
    "System note for ChatRaw bridge: this request is non-interactive. "
    "Do not use clarify or ask follow-up questions. If details are missing, "
    "make a reasonable assumption and proceed. Prefer bounded commands and "
    "concise summaries. Runtime context: this bridge runs on the RM01 host "
    "10.10.99.99 as user rm01. When the user asks about the 99 server, "
    "inspect this local host directly unless the user explicitly asks for SSH. "
    "For long-running operations such as docker compose pull/up/build, npm install, "
    "pip install, model downloads, large builds, package managers, or server/watch "
    "processes, start the operation in the background with output redirected to a "
    "clear log file, return the PID/job id and log path, then poll readiness with "
    "short bounded commands such as ps, tail -20, curl health checks, or docker ps. "
    "Do not keep a foreground terminal call waiting for the whole long operation."
)
CAPABILITY_SELF_DESCRIPTION_INSTRUCTION = (
    "When the user asks about your capabilities, skills, tools, or what you "
    "can do, answer from the local runtime and available tool context; do not "
    "call web_search unless the user explicitly asks for current external "
    "information."
)
NONINTERACTIVE_INSTRUCTION = "\n".join(
    part
    for part in (
        os.environ.get("HERMES_BRIDGE_NONINTERACTIVE_INSTRUCTION", DEFAULT_NONINTERACTIVE_INSTRUCTION),
        CAPABILITY_SELF_DESCRIPTION_INSTRUCTION,
    )
    if part
)
TOOL_ARGS_CAP = int(os.environ.get("HERMES_BRIDGE_TOOL_ARGS_CAP", "4000"))
TOOL_RESULT_CAP = int(os.environ.get("HERMES_BRIDGE_TOOL_RESULT_CAP", "12000"))
AUTO_APPROVE_LABEL = "ChatRaw bridge auto-approved this Hermes request once."


app = FastAPI(title="Hermes ChatRaw Structured Bridge")


def _db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            key TEXT PRIMARY KEY,
            hermes_session_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    return conn


def _get_session(key: str) -> Optional[str]:
    with _db() as conn:
        row = conn.execute("SELECT hermes_session_id FROM sessions WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None


def _save_session(key: str, session_id: str) -> None:
    now = int(time.time())
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO sessions(key, hermes_session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                hermes_session_id = excluded.hermes_session_id,
                updated_at = excluded.updated_at
            """,
            (key, session_id, now, now),
        )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif item.get("text"):
                    parts.append(str(item.get("text")))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def _latest_user(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            text = _message_text(message.get("content")).strip()
            if text:
                return text
    return ""


def _transcript(messages: List[Dict[str, Any]]) -> str:
    role_names = {"system": "System", "user": "User", "assistant": "Assistant"}
    lines: List[str] = []
    for message in messages:
        text = _message_text(message.get("content")).strip()
        if not text:
            continue
        role = role_names.get(str(message.get("role", "")).lower(), "Message")
        lines.append(f"{role}:\n{text}")
    return "\n\n".join(lines)


def _noninteractive_query(text: str) -> str:
    text = text.strip()
    if not NONINTERACTIVE_INSTRUCTION:
        return text
    return f"{NONINTERACTIVE_INSTRUCTION}\n\n{text}"


def _session_key(request: Request, body: Dict[str, Any]) -> str:
    for header in ("x-hermes-session-id", "x-hermes-session-key", "x-chatraw-chat-id", "x-conversation-id"):
        value = request.headers.get(header)
        if value and value.strip():
            return f"header:{value.strip()}"
    if value := body.get("session_id") or body.get("conversation_id"):
        return f"body:{str(value).strip()}"
    messages = body.get("messages") or []
    first_user = _latest_user(messages) if isinstance(messages, list) else ""
    return f"fallback:{uuid.uuid5(uuid.NAMESPACE_URL, first_user or str(time.time()))}"


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _json_or_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _parse_tool_ok(result_text: str) -> Optional[bool]:
    try:
        value = json.loads(result_text)
    except Exception:
        return None
    if isinstance(value, dict):
        if isinstance(value.get("ok"), bool):
            return value["ok"]
        if isinstance(value.get("success"), bool):
            return value["success"]
        if isinstance(value.get("exit_code"), int):
            return value["exit_code"] == 0
        if isinstance(value.get("returncode"), int):
            return value["returncode"] == 0
    return None


def _load_latest_session_tool_results(session_id: str) -> Dict[str, Dict[str, Any]]:
    """Read Hermes state.db and return tool details for the latest user turn."""
    if not session_id or not Path(HERMES_STATE_DB).exists():
        return {}
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, role, content, tool_call_id, tool_calls, tool_name, timestamp
            FROM messages
            WHERE session_id = ? AND active = 1
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()

    if not rows:
        return {}

    start_index = 0
    for idx in range(len(rows) - 1, -1, -1):
        if rows[idx]["role"] == "user":
            start_index = idx + 1
            break

    calls: Dict[str, Dict[str, Any]] = {}
    for row in rows[start_index:]:
        role = row["role"]
        if role == "assistant" and row["tool_calls"]:
            try:
                raw_calls = json.loads(row["tool_calls"])
            except Exception:
                raw_calls = []
            if not isinstance(raw_calls, list):
                continue
            for index, raw_call in enumerate(raw_calls):
                if not isinstance(raw_call, dict):
                    continue
                function = raw_call.get("function") if isinstance(raw_call.get("function"), dict) else {}
                call_id = raw_call.get("id") or raw_call.get("call_id") or f"{session_id}:{row['id']}:{index}"
                args = function.get("arguments", raw_call.get("arguments"))
                calls[str(call_id)] = {
                    "id": str(call_id),
                    "name": function.get("name") or raw_call.get("name") or "tool",
                    "args": _truncate(_json_or_text(args), TOOL_ARGS_CAP),
                    "result": None,
                    "ok": None,
                    "truncated": False,
                }
        elif role == "tool" and row["tool_call_id"]:
            call_id = str(row["tool_call_id"])
            result_raw = _json_or_text(row["content"])
            call = calls.setdefault(
                call_id,
                {
                    "id": call_id,
                    "name": row["tool_name"] or "tool",
                    "args": "",
                    "result": None,
                    "ok": None,
                    "truncated": False,
                },
            )
            call["name"] = row["tool_name"] or call.get("name") or "tool"
            call["result"] = _truncate(result_raw, TOOL_RESULT_CAP)
            call["truncated"] = len(result_raw) > TOOL_RESULT_CAP
            call["ok"] = _parse_tool_ok(result_raw)
    return calls


class GatewayConnection:
    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.pending: Dict[str, asyncio.Future] = {}
        self.handlers: List[Callable[[Dict[str, Any]], Awaitable[None]]] = []
        self.next_id = 1
        self.ready = asyncio.Event()
        self.stderr_tail = ""
        self.stdout_task: Optional[asyncio.Task] = None
        self.stderr_task: Optional[asyncio.Task] = None
        self.last_activity = time.time()

    async def start(self) -> None:
        if self.alive:
            if self.ready.is_set():
                return
        root = Path(HERMES_AGENT_ROOT)
        python = Path(HERMES_PYTHON)
        if not (root / "tui_gateway" / "entry.py").exists():
            raise RuntimeError(f"tui_gateway not found under {root}")
        if not python.exists():
            raise RuntimeError(f"Hermes Python not found: {python}")

        self.ready.clear()
        env = os.environ.copy()
        env.update(
            {
                "HERMES_PYTHON_SRC_ROOT": str(root),
                "HERMES_NO_COLOR": "1",
                "NO_COLOR": "1",
                "TERM": "dumb",
                "PYTHONUNBUFFERED": "1",
            }
        )
        self.proc = await asyncio.create_subprocess_exec(
            str(python),
            "-m",
            "tui_gateway.entry",
            cwd=str(root),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.stdout_task = asyncio.create_task(self._read_stdout())
        self.stderr_task = asyncio.create_task(self._read_stderr())
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=READY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            await self.close()
            note = f"; stderr: {self.stderr_tail.strip()}" if self.stderr_tail.strip() else ""
            raise RuntimeError(f"tui_gateway did not become ready within {READY_TIMEOUT_SECONDS}s{note}") from exc

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                frame = json.loads(line.decode("utf-8", errors="replace").strip())
            except json.JSONDecodeError:
                continue
            await self._handle_frame(frame)
        await self._fail_pending(RuntimeError("tui_gateway connection closed"))

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            chunk = await self.proc.stderr.read(1024)
            if not chunk:
                break
            self.stderr_tail = (self.stderr_tail + chunk.decode("utf-8", errors="replace"))[-4000:]

    async def _handle_frame(self, frame: Dict[str, Any]) -> None:
        if frame.get("method") == "event" and isinstance(frame.get("params"), dict):
            event = frame["params"]
            if event.get("type") == "gateway.ready":
                self.ready.set()
            for handler in list(self.handlers):
                try:
                    await handler(event)
                except Exception:
                    pass
            return

        request_id = str(frame.get("id"))
        future = self.pending.pop(request_id, None)
        if not future or future.done():
            return
        if frame.get("error"):
            error = frame["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            future.set_exception(RuntimeError(message or "gateway RPC error"))
        else:
            future.set_result(frame.get("result") or {})

    async def _fail_pending(self, exc: Exception) -> None:
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(exc)
        self.pending.clear()

    def on_event(self, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> Callable[[], None]:
        self.handlers.append(handler)

        def unsubscribe() -> None:
            try:
                self.handlers.remove(handler)
            except ValueError:
                pass

        return unsubscribe

    async def request(self, method: str, params: Dict[str, Any], timeout: float = RPC_TIMEOUT_SECONDS) -> Dict[str, Any]:
        if not self.alive:
            raise RuntimeError("tui_gateway is not running")
        assert self.proc and self.proc.stdin
        self.last_activity = time.time()
        request_id = str(self.next_id)
        self.next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self.pending[request_id] = future
        frame = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, ensure_ascii=False)
        self.proc.stdin.write((frame + "\n").encode("utf-8"))
        await self.proc.stdin.drain()
        return await asyncio.wait_for(future, timeout=timeout)

    async def close(self) -> None:
        await self._fail_pending(RuntimeError("tui_gateway connection closed"))
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        for task in (self.stdout_task, self.stderr_task):
            if task and not task.done():
                task.cancel()
        self.proc = None


_gateway: Optional[GatewayConnection] = None
_gateway_lock = asyncio.Lock()


async def _get_gateway() -> GatewayConnection:
    global _gateway
    async with _gateway_lock:
        if _gateway and _gateway.alive:
            await _gateway.start()
            return _gateway
        _gateway = GatewayConnection()
        await _gateway.start()
        return _gateway


class RunState:
    def __init__(self, run_id: str, key: str, body: Dict[str, Any], request_headers: Dict[str, str]) -> None:
        self.run_id = run_id
        self.key = key
        self.body = body
        self.request_headers = request_headers
        self.queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()
        self.task: Optional[asyncio.Task] = None
        self.gateway_session_id = ""
        self.stored_session_id = ""
        self.created_at = time.time()
        self.finished = False

    async def put(self, event: Dict[str, Any]) -> None:
        await self.queue.put(event)

    async def finish(self) -> None:
        self.finished = True
        await self.queue.put(None)


_runs: Dict[str, RunState] = {}
_runs_lock = asyncio.Lock()


def _run_query(run: RunState) -> str:
    messages = run.body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="messages is required")
    if _get_session(run.key):
        query = _latest_user(messages)
    else:
        query = _transcript(messages) or _latest_user(messages)
    if not query:
        raise HTTPException(status_code=400, detail="No user message found")
    return _noninteractive_query(query)


async def _emit_run_error(run: RunState, message: str) -> None:
    await run.put({"type": "error", "error": {"message": message}, "status": "error"})
    await run.finish()


async def _run_gateway_turn(run: RunState) -> None:
    gateway = await _get_gateway()
    existing_session = _get_session(run.key)
    query = _run_query(run)
    completion = asyncio.get_running_loop().create_future()
    sent_text = False
    saw_reasoning_delta = False
    approval_count = 0

    async def handle_gateway_event(event: Dict[str, Any]) -> None:
        nonlocal sent_text, saw_reasoning_delta, approval_count
        if event.get("session_id") != run.gateway_session_id:
            return
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        event_type = str(event.get("type") or "")

        if event_type == "message.delta":
            delta = _as_text(payload.get("text"))
            if delta:
                sent_text = True
                await run.put({"type": "message.delta", "delta": {"content": delta}})
        elif event_type == "reasoning.delta":
            delta = _as_text(payload.get("text"))
            if delta:
                saw_reasoning_delta = True
                await run.put({"type": "reasoning.delta", "delta": {"reasoning": delta}})
        elif event_type == "reasoning.available":
            text = _as_text(payload.get("text"))
            if text and not saw_reasoning_delta:
                await run.put({"type": "reasoning.delta", "delta": {"reasoning": text}})
        elif event_type == "tool.start":
            await run.put(
                {
                    "type": "tool.start",
                    "tool_id": _as_text(payload.get("tool_id")),
                    "name": _as_text(payload.get("name")) or "tool",
                    "context": _as_text(payload.get("context")),
                }
            )
        elif event_type == "tool.complete":
            await run.put(
                {
                    "type": "tool.complete",
                    "tool_id": _as_text(payload.get("tool_id")),
                    "name": _as_text(payload.get("name")) or "tool",
                    "summary": _as_text(payload.get("summary")),
                }
            )
        elif event_type == "approval.request":
            approval_count += 1
            tool_id = _as_text(payload.get("tool_id")) or f"approval-{run.run_id}-{approval_count}"
            reason = (
                _as_text(payload.get("reason"))
                or _as_text(payload.get("message"))
                or _as_text(payload.get("summary"))
                or "Hermes requested approval"
            )
            await run.put(
                {
                    "type": "tool.start",
                    "tool_id": tool_id,
                    "name": "approval",
                    "context": f"{reason} ({AUTO_APPROVE_LABEL})",
                }
            )
            await run.put(
                {
                    "type": "tool.complete",
                    "tool_id": tool_id,
                    "name": "approval",
                    "summary": AUTO_APPROVE_LABEL,
                    "result": AUTO_APPROVE_LABEL,
                }
            )

            async def approve_once() -> None:
                try:
                    await gateway.request(
                        "approval.respond",
                        {"session_id": run.gateway_session_id, "choice": "once"},
                        timeout=15,
                    )
                except Exception as exc:
                    await run.put(
                        {
                            "type": "tool.error",
                            "tool_id": tool_id,
                            "name": "approval",
                            "summary": f"Approval failed: {exc}",
                            "result": str(exc),
                        }
                    )

            asyncio.create_task(approve_once())
        elif event_type == "message.complete":
            text = _as_text(payload.get("text"))
            status = _as_text(payload.get("status")) or "complete"
            if text and not sent_text and status == "complete":
                await run.put({"type": "message.delta", "delta": {"content": text}})
            if status == "complete":
                tool_details = await asyncio.to_thread(
                    _load_latest_session_tool_results,
                    run.stored_session_id,
                )
                for tool_id, detail in tool_details.items():
                    await run.put(
                        {
                            "type": "tool.complete",
                            "tool_id": tool_id,
                            "name": detail.get("name") or "tool",
                            "arguments": detail.get("args") or "",
                            "result": detail.get("result"),
                            "ok": detail.get("ok"),
                            "truncated": detail.get("truncated", False),
                        }
                    )
                await run.put({"type": "message.completed", "status": "completed"})
            else:
                message = text or f"Hermes turn ended with status {status}"
                await run.put({"type": "error", "error": {"message": message}, "status": "error"})
            if not completion.done():
                completion.set_result(None)
        elif event_type == "error":
            message = _as_text(payload.get("message")) or "gateway reported an error"
            await run.put({"type": "error", "error": {"message": message}, "status": "error"})
            if not completion.done():
                completion.set_result(None)

    unsubscribe: Optional[Callable[[], None]] = None
    try:
        if existing_session:
            resumed = await gateway.request(
                "session.resume",
                {"session_id": existing_session, "cols": 200},
            )
            run.gateway_session_id = str(resumed.get("session_id") or "")
            run.stored_session_id = existing_session
        else:
            created = await gateway.request(
                "session.create",
                {"cols": 200, "cwd": WORKDIR},
            )
            run.gateway_session_id = str(created.get("session_id") or "")
            run.stored_session_id = str(created.get("stored_session_id") or "")

        if not run.gateway_session_id or not run.stored_session_id:
            raise RuntimeError("gateway did not return a usable session")
        _save_session(run.key, run.stored_session_id)

        unsubscribe = gateway.on_event(handle_gateway_event)
        prompt_task = asyncio.create_task(
            gateway.request("prompt.submit", {"session_id": run.gateway_session_id, "text": query}, timeout=TURN_TIMEOUT_SECONDS)
        )
        prompt_task.add_done_callback(
            lambda task: (
                None
                if task.cancelled() or task.exception() is None or completion.done()
                else completion.set_exception(task.exception())
            )
        )
        await asyncio.wait_for(completion, timeout=TURN_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        if run.gateway_session_id:
            try:
                await gateway.request("session.interrupt", {"session_id": run.gateway_session_id}, timeout=15)
            except Exception:
                pass
        raise
    except Exception as exc:
        await _emit_run_error(run, str(exc))
        return
    finally:
        gateway.last_activity = time.time()
        if unsubscribe:
            unsubscribe()
    await run.finish()


def _prune_runs() -> None:
    now = time.time()
    stale = [run_id for run_id, run in _runs.items() if run.finished and now - run.created_at > RUN_TTL_SECONDS]
    for run_id in stale:
        _runs.pop(run_id, None)


async def _maybe_close_idle_gateway() -> None:
    global _gateway
    if _gateway and _gateway.alive and time.time() - _gateway.last_activity > IDLE_GATEWAY_SECONDS:
        await _gateway.close()
        _gateway = None


def _sse(event: Dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    return f"event: {event_type}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.on_event("shutdown")
async def shutdown() -> None:
    if _gateway:
        await _gateway.close()


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "model": MODEL_ID,
        "mode": "structured-runs",
        "agent_root": HERMES_AGENT_ROOT,
        "python": HERMES_PYTHON,
        "runs": len(_runs),
        "gateway_alive": bool(_gateway and _gateway.alive),
    }


@app.get("/v1/models")
async def models() -> Dict[str, Any]:
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "hermes-cli"}]}


@app.post("/v1/runs")
async def create_run(request: Request) -> Dict[str, str]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    run_id = f"run-{uuid.uuid4().hex}"
    run = RunState(run_id, _session_key(request, body), body, dict(request.headers))
    async with _runs_lock:
        _prune_runs()
        _runs[run_id] = run
    run.task = asyncio.create_task(_run_gateway_turn(run))
    return {"id": run_id, "run_id": run_id, "object": "run", "status": "queued"}


@app.get("/v1/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")

    async def generate():
        last_heartbeat = time.time()
        while True:
            try:
                event = await asyncio.wait_for(run.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                if time.time() - last_heartbeat >= 15:
                    last_heartbeat = time.time()
                    yield ": ping\n\n"
                continue
            if event is None:
                break
            yield _sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/runs/{run_id}/stop")
async def stop_run(run_id: str) -> Dict[str, Any]:
    run = _runs.get(run_id)
    if not run:
        return {"success": True, "stopped": False}
    if run.task and not run.task.done():
        run.task.cancel()
    if run.gateway_session_id:
        try:
            gateway = await _get_gateway()
            await gateway.request("session.interrupt", {"session_id": run.gateway_session_id}, timeout=15)
        except Exception:
            pass
    await run.finish()
    return {"success": True, "stopped": True}


async def _collect_run_events(run: RunState) -> Dict[str, Any]:
    content = ""
    thinking = ""
    tools: List[Dict[str, Any]] = []
    while True:
        event = await run.queue.get()
        if event is None:
            break
        event_type = str(event.get("type") or "")
        if event_type == "message.delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            content += _as_text(delta.get("content"))
        elif event_type == "reasoning.delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            thinking += _as_text(delta.get("reasoning"))
        elif event_type.startswith("tool."):
            tools.append(event)
        elif event_type == "error":
            error = event.get("error") if isinstance(event.get("error"), dict) else {}
            raise HTTPException(status_code=502, detail=_as_text(error.get("message")) or "Hermes run failed")
    return {"content": content, "thinking": thinking, "tools": tools, "session_id": run.stored_session_id}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")
    stream = bool(body.get("stream"))
    create_req = Request(request.scope, request.receive)
    run_id = f"run-{uuid.uuid4().hex}"
    run = RunState(run_id, _session_key(request, body), body, dict(request.headers))
    async with _runs_lock:
        _runs[run_id] = run
    run.task = asyncio.create_task(_run_gateway_turn(run))

    if stream:
        async def generate():
            chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
            while True:
                event = await run.queue.get()
                if event is None:
                    break
                event_type = str(event.get("type") or "")
                if event_type == "message.delta":
                    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                    text = _as_text(delta.get("content"))
                    if text:
                        yield "data: " + json.dumps(
                            {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "model": MODEL_ID,
                                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                            },
                            ensure_ascii=False,
                        ) + "\n\n"
                elif event_type == "reasoning.delta":
                    delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                    text = _as_text(delta.get("reasoning"))
                    if text:
                        yield "data: " + json.dumps(
                            {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "model": MODEL_ID,
                                "choices": [{"index": 0, "delta": {"reasoning_content": text, "thinking": text}, "finish_reason": None}],
                            },
                            ensure_ascii=False,
                        ) + "\n\n"
                elif event_type == "error":
                    error = event.get("error") if isinstance(event.get("error"), dict) else {}
                    yield "data: " + json.dumps({"error": {"message": _as_text(error.get("message"))}}, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    result = await _collect_run_events(run)
    message = {"role": "assistant", "content": result["content"]}
    if result["thinking"]:
        message["reasoning_content"] = result["thinking"]
        message["thinking"] = result["thinking"]
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "model": MODEL_ID,
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "hermes_session_id": result["session_id"],
        }
    )


@app.on_event("startup")
async def startup_reaper() -> None:
    async def loop() -> None:
        while True:
            await asyncio.sleep(60)
            _prune_runs()
            await _maybe_close_idle_gateway()

    asyncio.create_task(loop())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
