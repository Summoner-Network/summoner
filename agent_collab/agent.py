import argparse
import asyncio
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from codex_app_server.client import AppServerClient, AppServerConfig
from codex_app_server.generated.v2_all import AgentMessageDeltaNotification, TurnCompletedNotification
from codex_app_server.models import Notification
from summoner.client import SummonerClient
from summoner.protocol import Direction, Node
from summoner.visionary import ClientFlowVisualizer


@dataclass(slots=True)
class TurnOutput:
    request_id: str
    thread_id: str
    turn_id: str
    status: str
    error: Optional[str]
    prompt: str
    response: str


@dataclass(slots=True)
class PendingRemoteCollab:
    request_id: str
    sender: str
    prompt: str
    ts: int


def _supports_ansi() -> bool:
    term = str(os.environ.get("TERM", ""))
    if term in {"", "dumb"}:
        return False
    return True


def status_to_str(status_obj: Any) -> str:
    root = getattr(status_obj, "root", status_obj)
    value = getattr(root, "value", None)
    if isinstance(value, str):
        return value
    if isinstance(root, str):
        return root
    return str(root)


class InteractiveCodexSummonerAgent:
    """
    Interactive terminal agent:
    - local user chats with local Codex
    - awareness updates are broadcast on channel='awareness'
    - actionable remote requests are sent/handled on channel='collab'
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        if not hasattr(self.args, "show_awareness_live"):
            self.args.show_awareness_live = False
        if not hasattr(self.args, "local_wait_interval"):
            self.args.local_wait_interval = 0.30
        if not hasattr(self.args, "collab_mode"):
            self.args.collab_mode = "open"
        if not hasattr(self.args, "allowed_peer"):
            self.args.allowed_peer = []
        if not hasattr(self.args, "max_collab_chars"):
            self.args.max_collab_chars = 4000
        if not hasattr(self.args, "max_pending_review_collabs"):
            self.args.max_pending_review_collabs = 20
        if not hasattr(self.args, "collab_wait_notify_seconds"):
            self.args.collab_wait_notify_seconds = 15.0
        if not hasattr(self.args, "collab_max_wait_seconds"):
            self.args.collab_max_wait_seconds = 300.0
        if not hasattr(self.args, "remote_collab_timeout_seconds"):
            self.args.remote_collab_timeout_seconds = 180.0
        if not hasattr(self.args, "collab_stream_flush_seconds"):
            self.args.collab_stream_flush_seconds = 0.08
        if not hasattr(self.args, "shared_secret"):
            self.args.shared_secret = ""
        if not hasattr(self.args, "sig_ttl_seconds"):
            self.args.sig_ttl_seconds = 300
        if not hasattr(self.args, "state_file"):
            self.args.state_file = None
        if not hasattr(self.args, "no_color"):
            self.args.no_color = False
        if not hasattr(self.args, "ui_backend"):
            self.args.ui_backend = "auto"
        if not hasattr(self.args, "history_file"):
            self.args.history_file = None
        self.agent = SummonerClient(name=args.name)
        self.flow = self.agent.flow().activate()
        self.flow.add_arrow_style(stem="-", brackets=("[", "]"), separator=",", tip=">")
        self.flow.triggers_file = str(Path(__file__).with_name("TRIGGERS"))
        # Load trigger tree from local TRIGGERS file (same folder as this script).
        self.Trigger = self.flow.triggers()
        self.viz: Optional[ClientFlowVisualizer] = None

        self.turn_lock = asyncio.Lock()
        self.state_lock = threading.Lock()
        self.awareness_queue: asyncio.Queue = asyncio.Queue()
        self.collab_queue: asyncio.Queue = asyncio.Queue()
        self.input_queue: asyncio.Queue = asyncio.Queue()

        self.collab_mode = self._state_token(args.collab_mode)
        self.allowed_peers = set(args.allowed_peer or [])

        # Parallel pipeline states (Summoner upload/download surface)
        self.pipeline_states: dict[str, str] = {
            "local_user": "waiting_input",
            "collab_pipeline": "idle",
            "awareness_pipeline": "idle",
            "collab_mode": self.collab_mode,
            "remote_review_queue": "idle",
        }
        self.peer_state: dict[str, str] = dict(self.pipeline_states)

        self.pending_collab_requests: dict[str, str] = {}
        self.pending_collab_wait_tasks: dict[str, asyncio.Task] = {}
        self.pending_collab_started_at: dict[str, float] = {}
        self.pending_collab_stream_started: set[str] = set()
        self.pending_remote_collabs: dict[str, PendingRemoteCollab] = {}
        self.awareness_history: deque[str] = deque(maxlen=max(5, args.awareness_history))

        self._tasks_started = False
        self._shutdown_requested = False
        self._stop_input = threading.Event()
        self._input_enabled = threading.Event()
        self._input_enabled.set()
        self._input_thread: Optional[threading.Thread] = None
        self._console_lock = threading.Lock()
        self._status_active = False
        self._using_prompt_toolkit = False
        self._ptk_session = None
        self._ptk_prompt_async: Optional[Callable[..., Any]] = None
        self._ptk_patch_stdout = None
        self._ptk_file_history = None
        self._ptk_prompt_suspended_by_stream = False
        self._use_color = (not bool(self.args.no_color)) and _supports_ansi()
        self._ansi = {
            "reset": "\033[0m",
            "dim": "\033[2m",
            "codex": "\033[38;5;39m",
            "assistant": "\033[38;5;50m",
            "collab": "\033[38;5;214m",
            "remote": "\033[38;5;141m",
            "awareness": "\033[38;5;110m",
            "error": "\033[38;5;196m",
            "status": "\033[38;5;244m",
            "ok": "\033[38;5;41m",
            "warn": "\033[38;5;208m",
        }
        self._prompt_text = f"{self._role_label('codex')}> "
        self._prompt_plain = "codex> "
        self._ptk_prompt_text: Any = self._prompt_plain
        self._ux_cmd_collab = "/collab"
        self._ux_cmd_collab_pending = "/collab-pending"

        self.workspace_root = Path(args.workspace_root or (args.codex_cwd or ".")).resolve()
        self.workspace_id = hashlib.sha256(str(self.workspace_root).encode("utf-8")).hexdigest()[:12]
        self.last_workspace_fingerprint = ""
        self.shared_secret = str(args.shared_secret or "")
        self._nonce_lock = threading.Lock()
        self._seen_nonces: dict[str, int] = {}

        # Separate threads by usage key to avoid cross-talk between local and remote collab turns.
        self.thread_by_key: dict[str, str] = {}
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.args.name))
        self.state_file = Path(args.state_file) if args.state_file else Path(f".codex_live_collab_state_{safe_name}.json")
        self.history_file = (
            Path(args.history_file)
            if args.history_file
            else Path(f".codex_live_collab_history_{safe_name}.txt")
        )

        self.app = AppServerClient(
            config=AppServerConfig(
                cwd=args.codex_cwd,
                codex_bin=args.codex_bin,
            )
        )

        self._register_handlers()
        self._load_persisted_state()
        self._configure_input_backend()

    def _snapshot_states(self) -> dict[str, str]:
        with self.state_lock:
            out = dict(self.pipeline_states)
            for key, value in self.peer_state.items():
                if key.startswith("peer_"):
                    out[key] = value
            return out

    def _push_viz_states(self) -> None:
        if self.viz is None:
            return
        snapshot = self._snapshot_states()
        labels: list[str] = list(snapshot.values())
        # Add route-aligned labels so Visionary can highlight occupied nodes
        # even when pipeline values are semantic statuses (idle/waiting_input).
        if "local_user" in snapshot:
            labels.append("local_turn")
        if "awareness_pipeline" in snapshot:
            labels.append("awareness")
        if "collab_pipeline" in snapshot:
            labels.append("collab")
        self.viz.push_states(labels)

    def _port_available(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, int(port)))
            except OSError:
                return False
        return True

    def _viz_port_available(self, port: int) -> bool:
        # Visionary is a local UI server. Do not reuse Summoner server host here:
        # that host may be remote and invalid to bind on this machine.
        return self._port_available("127.0.0.1", int(port))

    def _select_viz_port(self) -> int:
        requested = int(self.args.viz_port)
        if self._viz_port_available(requested):
            return requested
        # Keep fallback bounded and deterministic.
        for candidate in range(requested + 1, requested + 40):
            if self._viz_port_available(candidate):
                self._console_print(
                    f"[viz] port {requested} is in use, falling back to {candidate}",
                    re_prompt=False,
                )
                return candidate
        raise RuntimeError(
            f"Visionary port allocation failed: no free port found in [{requested}, {requested + 39}]"
        )

    def _prompt_ready(self) -> bool:
        return (
            self._input_enabled.is_set()
            and not self._stop_input.is_set()
            and (not self.pending_collab_stream_started)
        )

    def _request_shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._stop_input.set()
        self._input_enabled.set()
        # Ensure Summoner main loop exits even when SIGINT is consumed by input layer.
        with contextlib.suppress(Exception):
            loop = getattr(self.agent, "loop", None)
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self.agent.shutdown)
            else:
                self.agent.shutdown()

    def _paint(self, text: str, color_key: str) -> str:
        if not self._use_color:
            return text
        return f"{self._ansi[color_key]}{text}{self._ansi['reset']}"

    def _role_label(self, role: str) -> str:
        color = {
            "codex": "codex",
            "assistant": "assistant",
            "collab": "collab",
            "remote-assistant": "remote",
            "awareness": "awareness",
            "error": "error",
        }.get(role, "status")
        text = {
            "collab": "collab",
        }.get(role, role)
        return self._paint(text, color)

    def _muted(self, text: str) -> str:
        if not self._use_color:
            return text
        return f"{self._ansi['dim']}{self._ansi['status']}{text}{self._ansi['reset']}"

    def _status_set(self, text: str) -> None:
        if self._using_prompt_toolkit and self._input_enabled.is_set():
            # Avoid raw CR status rendering while prompt_toolkit is actively
            # accepting input; this can corrupt prompt redraw.
            return
        with self._console_lock:
            # Do not paint transient status on top of an active remote stream line.
            if self.pending_collab_stream_started:
                return
            status_text = self._paint(text, "status")
            print(f"\r\033[2K{status_text}", end="", flush=True)
            self._status_active = True

    def _dots(self, idx: int) -> str:
        frames = [".", "..", "...", ".."]
        return frames[idx % len(frames)]

    def _status_clear(self, *, re_prompt: bool = True) -> None:
        with self._console_lock:
            if not self._status_active:
                return
            print("\r\033[2K", end="", flush=True)
            self._status_active = False
            if re_prompt and self._prompt_ready() and not self._using_prompt_toolkit:
                print(self._prompt_text, end="", flush=True)

    def _console_print(self, text: str = "", *, end: str = "\n", re_prompt: bool = False) -> None:
        with self._console_lock:
            if self._status_active:
                print("\r\033[2K", end="", flush=True)
                self._status_active = False
            if re_prompt and self._prompt_ready():
                if self._using_prompt_toolkit:
                    # prompt_toolkit manages its own prompt redraw; avoid CR/line-clear
                    # escape sequences here because they can merge with the prompt line.
                    print(text, end=end, flush=True)
                    return
                # Clear the current terminal line before printing async updates,
                # then restore a clean prompt.
                print("\r\033[2K", end="", flush=True)
                print(text, end=end, flush=True)
                if not self._using_prompt_toolkit:
                    print(self._prompt_text, end="", flush=True)
                return
            if self._using_prompt_toolkit and text and end == "\n":
                # Ensure normal prints start at column 0 to avoid occasional
                # right-shifted lines when prompt_toolkit just redrew the prompt.
                print("\r\033[2K", end="", flush=True)
            print(text, end=end, flush=True)

    def _configure_input_backend(self) -> None:
        requested = str(getattr(self.args, "ui_backend", "auto") or "auto").strip().lower()
        if requested not in {"auto", "threaded", "prompt_toolkit"}:
            requested = "auto"
        if requested == "threaded":
            self._using_prompt_toolkit = False
            self._enable_basic_line_editing()
            return

        try:
            prompt_toolkit = importlib.import_module("prompt_toolkit")
            history_mod = importlib.import_module("prompt_toolkit.history")
            patch_stdout_mod = importlib.import_module("prompt_toolkit.patch_stdout")
            formatted_text_mod = importlib.import_module("prompt_toolkit.formatted_text")
            prompt_session_cls = getattr(prompt_toolkit, "PromptSession")
            file_history_cls = getattr(history_mod, "FileHistory")
            self._ptk_patch_stdout = getattr(patch_stdout_mod, "patch_stdout")
            ansi_cls = getattr(formatted_text_mod, "ANSI")
        except Exception:
            self._using_prompt_toolkit = False
            self._enable_basic_line_editing()
            if requested == "prompt_toolkit":
                print(
                    "[ui] prompt_toolkit backend requested but package is unavailable. "
                    "Install with: python3 -m pip install prompt_toolkit"
                )
            elif requested == "auto" and sys.platform == "darwin":
                print(
                    "[ui] prompt_toolkit not installed; using basic input backend. "
                    "For robust arrows/history: python3 -m pip install prompt_toolkit"
                )
            return

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._ptk_file_history = file_history_cls(str(self.history_file))
        self._ptk_session = prompt_session_cls(history=self._ptk_file_history)
        self._ptk_prompt_async = self._ptk_session.prompt_async
        if self._use_color:
            # prompt_toolkit needs ANSI wrapper to render escape codes safely.
            self._ptk_prompt_text = ansi_cls(self._prompt_text)
        else:
            self._ptk_prompt_text = self._prompt_plain
        self._using_prompt_toolkit = True

    def _enable_basic_line_editing(self) -> None:
        # Best-effort: on some terminals Python may not load readline by default,
        # which causes arrow keys to print escape sequences.
        try:
            readline = importlib.import_module("readline")
        except Exception:
            return
        with contextlib.suppress(Exception):
            readline.parse_and_bind("tab: complete")
        with contextlib.suppress(Exception):
            readline.parse_and_bind("set editing-mode emacs")

    async def _prompt_toolkit_reader_loop(self) -> None:
        if not self._using_prompt_toolkit or self._ptk_prompt_async is None:
            return
        while not self._stop_input.is_set():
            if not self._input_enabled.is_set():
                await asyncio.sleep(0.05)
                continue
            try:
                if self._ptk_patch_stdout is not None:
                    with self._ptk_patch_stdout(raw=True):
                        line = await self._ptk_prompt_async(self._ptk_prompt_text)
                else:
                    line = await self._ptk_prompt_async(self._ptk_prompt_text)
            except EOFError:
                self._request_shutdown()
                break
            except KeyboardInterrupt:
                self._request_shutdown()
                break

            line = line.strip()
            if not line:
                continue

            self._input_enabled.clear()
            await self.input_queue.put(line)

            if line in {"/quit", "/exit"}:
                self._request_shutdown()
                break

    def _suspend_prompt_toolkit_stream_input(self) -> None:
        if (not self._using_prompt_toolkit) or self._ptk_prompt_suspended_by_stream:
            return
        self._ptk_prompt_suspended_by_stream = True
        self._input_enabled.clear()
        session = self._ptk_session
        app = getattr(session, "app", None) if session is not None else None
        if app is None:
            return
        with contextlib.suppress(Exception):
            is_running = getattr(app, "is_running", False)
            running = is_running() if callable(is_running) else bool(is_running)
            if running:
                app.exit(result="")

    async def _local_wait_indicator(self, stop_when: Optional[threading.Event] = None) -> None:
        frames = [".", "..", "...", ".."]
        idx = 0
        try:
            while not self._stop_input.is_set():
                if stop_when is not None and stop_when.is_set():
                    return
                if self.pending_collab_stream_started:
                    await asyncio.sleep(max(0.18, self.args.local_wait_interval))
                    continue
                frame = frames[idx % len(frames)]
                idx += 1
                self._status_set(f"{self._role_label('assistant')}> thinking{frame}   ")
                await asyncio.sleep(max(0.18, self.args.local_wait_interval))
        except asyncio.CancelledError:
            if stop_when is not None and stop_when.is_set():
                return
            self._status_clear()
            return

    def _state_token(self, raw: Any) -> str:
        """
        Summoner Node tokens only allow identifiers like [A-Za-z_]\\w*.
        Normalize dynamic state labels so upload_states never emits invalid tokens.
        """
        text = str(raw).strip().lower()
        if not text:
            return "unknown"
        text = re.sub(r"[^a-z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            text = "unknown"
        if not (text[0].isalpha() or text[0] == "_"):
            text = f"s_{text}"
        return text

    def _set_pipeline_state(self, key: str, state: str) -> None:
        with self.state_lock:
            normalized = self._state_token(state)
            self.pipeline_states[key] = normalized
            self.peer_state[key] = normalized
        self._push_viz_states()

    def _record_peer_state(self, key: str, state: str) -> None:
        with self.state_lock:
            self.peer_state[key] = self._state_token(state)
        self._push_viz_states()

    def _peer_state_key(self, sender: str) -> str:
        return f"peer_{self._state_token(sender)}"

    def _set_collab_mode(self, mode: str) -> None:
        self.collab_mode = self._state_token(mode)
        self._set_pipeline_state("collab_mode", self.collab_mode)

    def _refresh_review_queue_state(self) -> None:
        state = "queued" if self.pending_remote_collabs else "idle"
        self._set_pipeline_state("remote_review_queue", state)

    def _is_allowed_sender(self, sender: str) -> bool:
        return (not self.allowed_peers) or (sender in self.allowed_peers)

    def _signature_base(self, payload: dict) -> str:
        signed_fields = {
            "channel": payload.get("channel"),
            "kind": payload.get("kind"),
            "from": payload.get("from"),
            "to": payload.get("to"),
            "request_id": payload.get("request_id"),
            "prompt": payload.get("prompt"),
            "delta": payload.get("delta"),
            "status": payload.get("status"),
            "error": payload.get("error"),
            "response": payload.get("response"),
            "state": payload.get("state"),
            "changed_files_count": payload.get("changed_files_count"),
            "changed_files": payload.get("changed_files"),
            "workspace_id": payload.get("workspace_id"),
            "agent": payload.get("agent"),
            "ts": payload.get("ts"),
            "nonce": payload.get("nonce"),
        }
        return json.dumps(signed_fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def _compute_signature(self, payload: dict) -> str:
        key = self.shared_secret.encode("utf-8")
        msg = self._signature_base(payload).encode("utf-8")
        return hmac.new(key, msg, hashlib.sha256).hexdigest()

    def _purge_seen_nonces(self, now_ts: int) -> None:
        ttl = max(10, int(self.args.sig_ttl_seconds))
        cutoff = now_ts - (ttl * 2)
        stale = [nonce for nonce, ts in self._seen_nonces.items() if ts < cutoff]
        for nonce in stale:
            self._seen_nonces.pop(nonce, None)

    def _verify_signed_message(self, content: dict) -> bool:
        if not self.shared_secret:
            return True
        try:
            ts = int(content.get("ts", 0))
        except (TypeError, ValueError):
            return False
        nonce = str(content.get("nonce", ""))
        sig = str(content.get("sig", ""))
        if not nonce or not sig or ts <= 0:
            return False
        now_ts = int(time.time())
        ttl = max(10, int(self.args.sig_ttl_seconds))
        if abs(now_ts - ts) > ttl:
            return False
        expected = self._compute_signature(content)
        if not hmac.compare_digest(sig, expected):
            return False
        with self._nonce_lock:
            self._purge_seen_nonces(now_ts)
            if nonce in self._seen_nonces:
                return False
            self._seen_nonces[nonce] = now_ts
        return True

    def _persist_state(self) -> None:
        payload = {
            "version": 1,
            "collab_mode": self.collab_mode,
            "pending_remote_collabs": [
                {
                    "request_id": v.request_id,
                    "sender": v.sender,
                    "prompt": v.prompt,
                    "ts": v.ts,
                }
                for v in self.pending_remote_collabs.values()
            ],
            "pending_collab_requests": [
                {
                    "request_id": req_id,
                    "peer": peer,
                    "started_at": float(self.pending_collab_started_at.get(req_id, time.time())),
                }
                for req_id, peer in self.pending_collab_requests.items()
            ],
        }
        try:
            self.state_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            # Persistence should never break primary agent execution.
            pass

    def _load_persisted_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return
        mode = str(raw.get("collab_mode", self.collab_mode))
        self._set_collab_mode(mode)

        loaded_queue: dict[str, PendingRemoteCollab] = {}
        pending_remote_collabs = raw.get("pending_remote_collabs")
        if not isinstance(pending_remote_collabs, list):
            # Backward compatibility with pre-collab taxonomy state files.
            pending_remote_collabs = raw.get("pending_remote_intents", [])
        for item in pending_remote_collabs:
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("request_id", "")).strip()
            sender = str(item.get("sender", "")).strip()
            prompt = str(item.get("prompt", ""))
            try:
                ts = int(item.get("ts", int(time.time())))
            except (TypeError, ValueError):
                ts = int(time.time())
            if req_id and sender and prompt:
                loaded_queue[req_id] = PendingRemoteCollab(
                    request_id=req_id,
                    sender=sender,
                    prompt=prompt,
                    ts=ts,
                )
        self.pending_remote_collabs = loaded_queue
        self._refresh_review_queue_state()

        pending_collab_requests = raw.get("pending_collab_requests")
        if not isinstance(pending_collab_requests, list):
            # Backward compatibility with pre-collab taxonomy state files.
            pending_collab_requests = raw.get("pending_intent_requests", [])
        for item in pending_collab_requests:
            if not isinstance(item, dict):
                continue
            req_id = str(item.get("request_id", "")).strip()
            peer = str(item.get("peer", "")).strip()
            if not req_id or not peer:
                continue
            self.pending_collab_requests[req_id] = peer
            try:
                self.pending_collab_started_at[req_id] = float(item.get("started_at", time.time()))
            except (TypeError, ValueError):
                self.pending_collab_started_at[req_id] = time.time()

    def _stdin_reader(self, loop: asyncio.AbstractEventLoop) -> None:
        while not self._stop_input.is_set():
            if not self._input_enabled.wait(timeout=0.1):
                continue
            try:
                line = input(self._prompt_text).strip()
            except EOFError:
                self._request_shutdown()
                break
            except KeyboardInterrupt:
                self._request_shutdown()
                break

            if not line:
                continue

            self._input_enabled.clear()
            loop.call_soon_threadsafe(self.input_queue.put_nowait, line)

            if line in {"/quit", "/exit"}:
                self._request_shutdown()
                break

    async def _ensure_background_tasks(self) -> None:
        if self._tasks_started:
            return

        self._tasks_started = True
        loop = asyncio.get_running_loop()
        if self._using_prompt_toolkit:
            asyncio.create_task(self._prompt_toolkit_reader_loop())
        else:
            self._input_thread = threading.Thread(target=self._stdin_reader, args=(loop,), daemon=True)
            self._input_thread.start()

        asyncio.create_task(self._user_turn_loop())
        asyncio.create_task(self._workspace_watch_loop())

        await self._enqueue_awareness(
            {
                "channel": "awareness",
                "kind": "agent_status",
                "state": "online",
                "agent": self.args.name,
                "workspace_id": self.workspace_id,
            }
        )
        for request_id, peer in list(self.pending_collab_requests.items()):
            if request_id in self.pending_collab_wait_tasks:
                continue
            self.pending_collab_wait_tasks[request_id] = asyncio.create_task(
                self._collab_wait_indicator(request_id, str(peer))
            )
        self._push_viz_states()

    async def _enqueue_awareness(self, payload: dict) -> None:
        await self.awareness_queue.put(payload)

    async def _enqueue_collab(self, payload: dict) -> None:
        await self.collab_queue.put(payload)

    async def _broadcast_awareness(self, payload: dict) -> None:
        msg = {"channel": "awareness", **payload}
        await self._enqueue_awareness(msg)

    async def _send_collab(self, payload: dict) -> None:
        msg = {"channel": "collab", **payload}
        await self._enqueue_collab(msg)

    def _register_handlers(self) -> None:
        @self.agent.hook(direction=Direction.RECEIVE, priority=0)
        async def validate(msg: Any) -> Optional[dict]:
            if not (isinstance(msg, dict) and "content" in msg):
                return None
            content = msg.get("content")
            if not isinstance(content, dict):
                return None
            if not self._verify_signed_message(content):
                return None
            return content

        @self.agent.hook(direction=Direction.SEND)
        async def sign(outbound: Any) -> Optional[dict]:
            if isinstance(outbound, str):
                outbound = {"message": outbound}
            if not isinstance(outbound, dict):
                return None
            outbound.setdefault("from", self.args.name)
            if self.shared_secret:
                outbound.setdefault("ts", int(time.time()))
                outbound.setdefault("nonce", str(uuid.uuid4()))
                outbound["sig"] = self._compute_signature(outbound)
            return outbound

        @self.agent.upload_states()
        async def upload_states(content: Any) -> Any:
            sender = content.get("from") if isinstance(content, dict) else None
            inbound_channel = self._state_token("awareness")
            if isinstance(content, dict):
                inbound_channel = self._state_token(content.get("channel", "awareness"))
            self._push_viz_states()

            if sender is None:
                out = dict(self.peer_state)
                out["inbound"] = inbound_channel
                return out
            sender_key = str(sender)
            peer_key = self._peer_state_key(sender_key)
            return {
                "inbound": inbound_channel,
                "local_user": self.pipeline_states.get("local_user", "waiting_input"),
                "collab_pipeline": self.pipeline_states.get("collab_pipeline", "idle"),
                "awareness_pipeline": self.pipeline_states.get("awareness_pipeline", "idle"),
                "collab_mode": self.pipeline_states.get("collab_mode", "open"),
                "remote_review_queue": self.pipeline_states.get("remote_review_queue", "idle"),
                peer_key: self.peer_state.get(peer_key, "seen"),
            }

        @self.agent.download_states()
        async def download_states(possible_states: Any) -> None:
            if not isinstance(possible_states, dict):
                return

            local_options = possible_states.get("local_user", [])
            if Node("processing") in local_options:
                self._set_pipeline_state("local_user", "processing")
            elif Node("error") in local_options:
                self._set_pipeline_state("local_user", "error")
            elif Node("waiting_input") in local_options:
                self._set_pipeline_state("local_user", "waiting_input")

            collab_options = possible_states.get("collab_pipeline", [])
            if Node("processing") in collab_options:
                self._set_pipeline_state("collab_pipeline", "processing")
            elif Node("idle") in collab_options:
                self._set_pipeline_state("collab_pipeline", "idle")

            awareness_options = possible_states.get("awareness_pipeline", [])
            if Node("processing") in awareness_options:
                self._set_pipeline_state("awareness_pipeline", "processing")
            elif Node("idle") in awareness_options:
                self._set_pipeline_state("awareness_pipeline", "idle")

            mode_options = possible_states.get("collab_mode", [])
            if Node("locked") in mode_options:
                self._set_collab_mode("locked")
            elif Node("review_only") in mode_options:
                self._set_collab_mode("review_only")
            elif Node("open") in mode_options:
                self._set_collab_mode("open")

            self._push_viz_states()

        @self.agent.receive(route="awareness")
        async def on_awareness(content: dict) -> None:
            if str(content.get("channel", "awareness")) != "awareness":
                return
            sender = str(content.get("from", "unknown"))
            if sender == self.args.name:
                return
            asyncio.create_task(self._handle_remote_message(content))

        @self.agent.receive(route="collab")
        async def on_collab(content: dict) -> None:
            if str(content.get("channel", "awareness")) != "collab":
                return
            sender = str(content.get("from", "unknown"))
            if sender == self.args.name:
                return
            asyncio.create_task(self._handle_remote_message(content))

        @self.agent.send(route="awareness")
        async def send_awareness() -> Optional[dict]:
            await self._ensure_background_tasks()
            try:
                return self.awareness_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
                return None

        @self.agent.send(route="collab")
        async def send_collab() -> Optional[dict]:
            await self._ensure_background_tasks()
            try:
                return self.collab_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)
                return None

    async def _handle_remote_message(self, content: dict) -> None:
        sender = str(content.get("from", "unknown"))
        if not self._is_allowed_sender(sender):
            return
        channel = str(content.get("channel", "awareness"))
        kind = str(content.get("kind", "message"))

        self._record_peer_state(self._peer_state_key(sender), f"{channel}_{kind}")

        if channel == "awareness":
            self._set_pipeline_state("awareness_pipeline", "processing")
            summary = self._summarize_awareness(sender, content)
            self.awareness_history.append(summary)
            if self.args.show_awareness_live:
                self._console_print(f"\n[awareness:{sender}] {summary}", re_prompt=True)
            self._set_pipeline_state("awareness_pipeline", "idle")
            return

        if channel == "collab":
            if kind == "collab_request":
                await self._handle_remote_collab_request(content)
                return
            if kind == "collab_delta":
                self._handle_remote_collab_delta(content)
                return
            if kind == "collab_response":
                self._handle_remote_collab_response(content)
                return

        self._console_print(
            f"\n[remote:{sender}] channel={channel} kind={kind} payload={json.dumps(content, ensure_ascii=False)}",
            re_prompt=True,
        )

    def _summarize_awareness(self, sender: str, content: dict) -> str:
        kind = str(content.get("kind", "message"))
        if kind == "workspace_update":
            cnt = content.get("changed_files_count", 0)
            return f"workspace_update changed_files_count={cnt}"
        if kind == "chat_user_prompt":
            return f"chat_user_prompt {content.get('prompt', '')!r}"
        if kind == "chat_codex_response":
            status = content.get("status", "unknown")
            return f"chat_codex_response status={status}"
        if kind == "agent_status":
            return f"agent_status state={content.get('state', 'unknown')}"
        return f"{kind}"

    def _request_raw_dict(self, method: str, params: dict) -> dict:
        result = self.app._request_raw(method, params)  # noqa: SLF001 - SDK example compatibility fallback
        if not isinstance(result, dict):
            raise RuntimeError(f"{method} returned non-object result: {result!r}")
        return result

    def _extract_id(self, payload: dict, key: str) -> Optional[str]:
        node = payload.get(key)
        if isinstance(node, dict):
            nested = node.get("id") or node.get(f"{key}Id")
            if isinstance(nested, str):
                return nested
        direct = payload.get(f"{key}Id")
        if isinstance(direct, str):
            return direct
        return None

    def _ensure_thread(self, thread_key: str) -> str:
        existing = self.thread_by_key.get(thread_key)
        if existing:
            return existing

        try:
            started = self.app.thread_start({"model": self.args.model})
            thread_id = started.thread.id
        except Exception:
            raw = self._request_raw_dict("thread/start", {"model": self.args.model})
            thread_id = self._extract_id(raw, "thread")
            if thread_id is None:
                raise RuntimeError(f"Could not extract thread id from thread/start response: {raw!r}")

        self.thread_by_key[thread_key] = thread_id
        return thread_id

    def _run_turn_blocking(
        self,
        prompt: str,
        thread_key: str,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> TurnOutput:
        thread_id = self._ensure_thread(thread_key)
        request_id = str(uuid.uuid4())

        try:
            started = self.app.turn_start(thread_id=thread_id, input_items=prompt)
            turn_id = started.turn.id
        except Exception:
            raw = self._request_raw_dict(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                },
            )
            turn_id = self._extract_id(raw, "turn")
            if turn_id is None:
                raise RuntimeError(f"Could not extract turn id from turn/start response: {raw!r}")

        deltas: list[str] = []
        final_status = "unknown"
        final_error: Optional[str] = None
        last_item_id: Optional[str] = None

        while True:
            notification: Notification = self.app.next_notification()

            if (
                notification.method == "item/agentMessage/delta"
                and isinstance(notification.payload, AgentMessageDeltaNotification)
                and notification.payload.turn_id == turn_id
            ):
                chunk = notification.payload.delta
                item_id = notification.payload.item_id
                if last_item_id is not None and item_id != last_item_id:
                    # A turn can emit multiple assistant message items; keep
                    # natural spacing between them in non-streamed final text.
                    deltas.append("\n\n")
                    if on_delta is not None:
                        with contextlib.suppress(Exception):
                            on_delta("\n\n")
                last_item_id = item_id
                deltas.append(chunk)
                if on_delta is not None and chunk:
                    try:
                        on_delta(chunk)
                    except Exception:
                        # Streaming callback must not break turn processing.
                        pass
                continue

            if (
                notification.method == "turn/completed"
                and isinstance(notification.payload, TurnCompletedNotification)
                and notification.payload.turn.id == turn_id
            ):
                final_status = status_to_str(notification.payload.turn.status)
                if notification.payload.turn.error is not None:
                    final_error = notification.payload.turn.error.model_dump_json()
                break

        return TurnOutput(
            request_id=request_id,
            thread_id=thread_id,
            turn_id=turn_id,
            status=final_status,
            error=final_error,
            prompt=prompt,
            response="".join(deltas).strip(),
        )

    def _workspace_summary(self) -> dict:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.workspace_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=False,
            )
            lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "workspace_root": str(self.workspace_root),
            }

        fingerprint = "\n".join(lines)
        return {
            "ok": True,
            "workspace_id": self.workspace_id,
            "changed_files_count": len(lines),
            "changed_files": lines[: self.args.max_files_per_update],
            "fingerprint": fingerprint,
        }

    async def _workspace_watch_loop(self) -> None:
        while not self._stop_input.is_set():
            await asyncio.sleep(self.args.workspace_poll_seconds)
            if not self.args.share_workspace:
                continue

            snapshot = await asyncio.to_thread(self._workspace_summary)
            if not snapshot.get("ok"):
                continue

            fp = snapshot.get("fingerprint", "")
            if fp == self.last_workspace_fingerprint:
                continue

            self.last_workspace_fingerprint = fp
            await self._broadcast_awareness(
                {
                    "kind": "workspace_update",
                    "state": self.pipeline_states.get("local_user", "waiting_input"),
                    "workspace_id": snapshot["workspace_id"],
                    "changed_files_count": snapshot["changed_files_count"],
                    "changed_files": snapshot["changed_files"],
                    "ts": int(time.time()),
                }
            )

    async def _handle_remote_collab_request(self, content: dict) -> None:
        sender = str(content.get("from", "unknown"))
        if not self._is_allowed_sender(sender):
            return
        target = content.get("to")
        if target not in (None, self.args.name):
            return

        if not self.args.accept_remote_collabs:
            await self._reject_remote_collab(
                sender=sender,
                request_id=str(content.get("request_id", "")),
                reason="remote collab disabled on this agent",
            )
            return

        prompt = str(content.get("prompt") or content.get("message") or "")
        request_id = str(content.get("request_id") or uuid.uuid4())
        if not prompt:
            await self._reject_remote_collab(
                sender=sender,
                request_id=request_id,
                reason="missing prompt",
            )
            return
        if len(prompt) > self.args.max_collab_chars:
            await self._reject_remote_collab(
                sender=sender,
                request_id=request_id,
                reason=f"prompt too large ({len(prompt)} chars > max {self.args.max_collab_chars})",
            )
            return

        if self.collab_mode == "locked":
            await self._reject_remote_collab(
                sender=sender,
                request_id=request_id,
                reason="remote collab mode is locked",
            )
            return

        if self.collab_mode == "review_only":
            if len(self.pending_remote_collabs) >= self.args.max_pending_review_collabs:
                await self._reject_remote_collab(
                    sender=sender,
                    request_id=request_id,
                    reason="review queue is full",
                )
                return
            if request_id in self.pending_remote_collabs:
                await self._reject_remote_collab(
                    sender=sender,
                    request_id=request_id,
                    reason="duplicate request_id already queued",
                )
                return
            self.pending_remote_collabs[request_id] = PendingRemoteCollab(
                request_id=request_id,
                sender=sender,
                prompt=prompt,
                ts=int(time.time()),
            )
            self._refresh_review_queue_state()
            self._persist_state()
            preview = prompt.replace("\n", " ")
            if len(preview) > 160:
                preview = f"{preview[:157]}..."
            self._console_print(self._muted(f"[collab:{sender}] queued collab request (review_only)"), re_prompt=True)
            id_label = self._paint("  id:", "remote")
            self._console_print(f"{id_label} {request_id}", re_prompt=True)
            prompt_label = self._paint("  prompt:", "remote")
            prompt_value = f" {preview!r}"
            self._console_print(f"{prompt_label}{prompt_value}", re_prompt=True)
            approve_cmd = self._paint(f"/approve {request_id}", "ok")
            reject_cmd = self._paint(f"/reject {request_id}", "error")
            actions_label = self._paint("  actions:", "remote")
            self._console_print(f"{actions_label} {approve_cmd} | {reject_cmd}", re_prompt=True)
            return

        await self._execute_remote_collab(sender=sender, request_id=request_id, prompt=prompt)

    async def _reject_remote_collab(self, sender: str, request_id: str, reason: str) -> None:
        await self._send_collab(
            {
                "kind": "collab_response",
                "to": sender,
                "request_id": request_id,
                "status": "rejected",
                "error": reason,
                "response": "",
                "ts": int(time.time()),
            }
        )
        self._console_print(f"[collab:{sender}] rejected request_id={request_id}: {reason}", re_prompt=True)

    async def _execute_remote_collab(self, sender: str, request_id: str, prompt: str) -> None:
        self._set_pipeline_state("collab_pipeline", "processing")
        self._console_print(
            self._muted(f"\n[collab:{sender}] request_id={request_id} prompt={prompt!r}"),
            re_prompt=True,
        )
        if self._using_prompt_toolkit:
            self._console_print(
                self._muted(f"[collab:{sender}] processing remote turn..."),
                re_prompt=True,
            )
        else:
            self._status_set(f"[{self._role_label('collab')}:{sender}] processing remote turn...")

        started_at = time.time()
        next_notify = 5
        first_delta_seen = threading.Event()
        timeout_warned = False

        async with self.turn_lock:
            indicator_stop = asyncio.Event()
            delta_chunks: list[str] = []
            delta_lock = threading.Lock()
            anim_idx = 0

            async def remote_indicator() -> None:
                nonlocal next_notify, timeout_warned, anim_idx
                try:
                    while not indicator_stop.is_set():
                        await asyncio.sleep(1.0)
                        if indicator_stop.is_set():
                            break
                        anim_idx += 1
                        elapsed = int(time.time() - started_at)
                        threshold = int(float(self.args.remote_collab_timeout_seconds))
                        if threshold > 0 and (not timeout_warned) and elapsed >= threshold:
                            timeout_warned = True
                            if self._using_prompt_toolkit:
                                self._console_print(
                                    self._paint(
                                        f"[collab:{sender}] exceeded threshold ({threshold}s); waiting safely...",
                                        "warn",
                                    ),
                                    re_prompt=True,
                                )
                            else:
                                self._status_set(
                                    f"[{self._role_label('collab')}:{sender}] exceeded threshold ({threshold}s); waiting safely..."
                                )
                        phase = "streaming remote output" if first_delta_seen.is_set() else "processing remote turn"
                        if self._using_prompt_toolkit:
                            if elapsed >= next_notify:
                                self._console_print(
                                    self._muted(f"[collab:{sender}] {phase} ({elapsed}s)"),
                                    re_prompt=True,
                                )
                        else:
                            self._status_set(
                                f"[{self._role_label('collab')}:{sender}] {phase}{self._dots(anim_idx)} ({elapsed}s)"
                            )
                        if elapsed >= next_notify:
                            next_notify += 5
                except asyncio.CancelledError:
                    return

            async def delta_flush_loop() -> None:
                interval = max(0.02, float(self.args.collab_stream_flush_seconds))
                try:
                    while not indicator_stop.is_set() or delta_chunks:
                        await asyncio.sleep(interval)
                        with delta_lock:
                            if not delta_chunks:
                                continue
                            merged = "".join(delta_chunks)
                            delta_chunks.clear()
                        await self._send_collab(
                            {
                                "kind": "collab_delta",
                                "to": sender,
                                "request_id": request_id,
                                "delta": merged,
                                "ts": int(time.time()),
                            }
                        )
                except asyncio.CancelledError:
                    return

            def on_remote_delta(chunk: str) -> None:
                if not chunk:
                    return
                first_delta_seen.set()
                with delta_lock:
                    delta_chunks.append(chunk)

            indicator_task = asyncio.create_task(remote_indicator())
            flush_task = asyncio.create_task(delta_flush_loop())
            try:
                # Use cooperative waiting to avoid abandoning a background turn-consumer thread.
                output = await asyncio.to_thread(
                    self._run_turn_blocking,
                    prompt,
                    f"collab::{sender}",
                    on_remote_delta,
                )
            except Exception as exc:
                indicator_stop.set()
                indicator_task.cancel()
                flush_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await indicator_task
                with contextlib.suppress(asyncio.CancelledError):
                    await flush_task
                err = str(exc)
                await self._send_collab(
                    {
                        "kind": "collab_response",
                        "to": sender,
                        "request_id": request_id,
                        "status": "failed",
                        "error": err,
                        "response": "",
                        "ts": int(time.time()),
                    }
                )
                self._set_pipeline_state("collab_pipeline", "idle")
                self._console_print(f"[collab:{sender}] failed: {err}", re_prompt=True)
                return
            else:
                indicator_stop.set()
                indicator_task.cancel()
                flush_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await indicator_task
                with contextlib.suppress(asyncio.CancelledError):
                    await flush_task
                self._status_clear()

        await self._send_collab(
            {
                "kind": "collab_response",
                "to": sender,
                "request_id": request_id,
                "status": output.status,
                "error": output.error,
                "thread_id": output.thread_id,
                "turn_id": output.turn_id,
                "prompt": prompt,
                "response": output.response,
                "ts": int(time.time()),
            }
        )
        self._set_pipeline_state("collab_pipeline", "idle")
        self._console_print(
            self._muted(f"[collab:{sender}] completed request_id={request_id} status={output.status}"),
            re_prompt=True,
        )

    async def _execute_approved_remote_collab(self, req: PendingRemoteCollab) -> None:
        try:
            await self._execute_remote_collab(
                sender=req.sender,
                request_id=req.request_id,
                prompt=req.prompt,
            )
        finally:
            # Queue state was already mutated before scheduling; persist regardless of outcome.
            self._persist_state()

    async def _collab_wait_indicator(self, request_id: str, peer: str) -> None:
        start = self.pending_collab_started_at.get(request_id, time.time())
        next_notify = max(1, int(self.args.collab_wait_notify_seconds))
        max_wait = float(self.args.collab_max_wait_seconds)
        try:
            while request_id in self.pending_collab_requests and not self._stop_input.is_set():
                await asyncio.sleep(max(0.8, self.args.collab_wait_interval))
                if request_id not in self.pending_collab_requests:
                    break
                elapsed = int(time.time() - start)
                if max_wait > 0 and elapsed >= int(max_wait):
                    self.pending_collab_requests.pop(request_id, None)
                    self.pending_collab_started_at.pop(request_id, None)
                    self.pending_collab_stream_started.discard(request_id)
                    self.pending_collab_wait_tasks.pop(request_id, None)
                    if self._using_prompt_toolkit and self._ptk_prompt_suspended_by_stream:
                        self._ptk_prompt_suspended_by_stream = False
                        self._input_enabled.set()
                    self._status_clear()
                    self._console_print(
                        self._paint(
                            f"[collab] request_id={request_id} timed out after {elapsed}s waiting for {peer}. "
                            "Check peer mode/allowlist/connectivity, then retry /collab.",
                            "warn",
                        ),
                        re_prompt=True,
                    )
                    self._persist_state()
                    break
                if not self._input_enabled.is_set():
                    # Avoid colliding with local assistant spinner/output.
                    continue
                should_notify = elapsed >= next_notify
                if should_notify:
                    next_notify += max(1, int(self.args.collab_wait_notify_seconds))
                if self._using_prompt_toolkit:
                    if should_notify:
                        # prompt_toolkit redraws the prompt itself; avoid transient status-line
                        # rendering here to prevent merged lines like "...(15s)codex>".
                        self._console_print(
                            self._muted(f"[collab] waiting for {peer}... ({elapsed}s)"),
                            re_prompt=True,
                        )
                elif should_notify:
                    # Threaded input cannot safely redraw while user is typing.
                    self._console_print(self._muted(f"[collab] still waiting for {peer} ({elapsed}s)"), re_prompt=True)
        except asyncio.CancelledError:
            return

    def _handle_remote_collab_response(self, content: dict) -> None:
        target = content.get("to")
        if target not in (None, self.args.name):
            return

        sender = str(content.get("from", "unknown"))
        request_id = str(content.get("request_id", ""))
        status = str(content.get("status", "unknown"))
        response = str(content.get("response", ""))
        expected_peer = self.pending_collab_requests.get(request_id)
        if expected_peer is None:
            return
        if sender != expected_peer:
            self._console_print(
                self._paint(
                    f"[collab] ignoring response for request_id={request_id} from {sender}; expected {expected_peer}",
                    "warn",
                ),
                re_prompt=True,
            )
            return

        self.pending_collab_requests.pop(request_id, None)
        self.pending_collab_started_at.pop(request_id, None)
        streamed = request_id in self.pending_collab_stream_started
        if streamed:
            self.pending_collab_stream_started.discard(request_id)
        task = self.pending_collab_wait_tasks.pop(request_id, None)
        if task is not None:
            task.cancel()
        self._status_clear(re_prompt=not streamed)

        if streamed:
            # End remote streamed line before any prompt-clearing status print.
            with self._console_lock:
                print("", flush=True)
            if self._using_prompt_toolkit and self._ptk_prompt_suspended_by_stream:
                self._ptk_prompt_suspended_by_stream = False
                self._input_enabled.set()
        self._console_print(
            self._muted(f"\n[collab-response:{sender}] request_id={request_id} status={status}"),
            re_prompt=True,
        )
        if (not streamed) and response:
            self._console_print(f"{self._role_label('remote-assistant')}> {response}", re_prompt=True)
        self._persist_state()

    def _handle_remote_collab_delta(self, content: dict) -> None:
        target = content.get("to")
        if target not in (None, self.args.name):
            return

        sender = str(content.get("from", "unknown"))
        request_id = str(content.get("request_id", ""))
        delta = str(content.get("delta", ""))
        if not request_id or not delta:
            return
        expected_peer = self.pending_collab_requests.get(request_id)
        if expected_peer is None:
            return
        if sender != expected_peer:
            self._console_print(
                self._paint(
                    f"[collab] ignoring delta for request_id={request_id} from {sender}; expected {expected_peer}",
                    "warn",
                ),
                re_prompt=True,
            )
            return

        with self._console_lock:
            if request_id not in self.pending_collab_stream_started:
                self.pending_collab_stream_started.add(request_id)
                if self._status_active:
                    print("\r\033[2K", end="", flush=True)
                    self._status_active = False
                if self._using_prompt_toolkit:
                    # Suspend prompt redraw while remote stream is active; otherwise
                    # prompt_toolkit can append `codex>` to streamed text mid-line.
                    self._suspend_prompt_toolkit_stream_input()
                    if float(self.args.collab_max_wait_seconds) <= 0:
                        print(
                            self._muted(
                                "[ui] input paused during remote stream; "
                                "--collab-max-wait-seconds=0 means pause lasts until final response."
                            ),
                            flush=True,
                        )
                    print("", flush=True)
                else:
                    print("\r\033[2K", end="", flush=True)
                print(self._muted(f"[collab-stream:{sender}] request_id={request_id}"), flush=True)
                print(f"{self._role_label('remote-assistant')}> ", end="", flush=True)
            print(delta, end="", flush=True)

    def _print_help(self) -> None:
        lines = [
            "Commands:",
            "  /help                         Show this help",
            "  /quit | /exit                 Stop the agent",
            "  /context                      Show recent awareness summaries",
            "  /pending | /collab-pending    Show local pending outbound collab requests",
            "  /mode <open|review|locked>    Set remote collab handling mode",
            "  /review                       List queued remote collab requests",
            "  /approve <request_id|next>    Execute a queued remote collab request",
            "  /reject <request_id|next>     Reject a queued remote collab request",
            "  /collab <peer> <prompt>       Send actionable request to remote peer",
            "  /local <prompt>               Run prompt locally (same as plain text)",
            "  <plain text>                  Run prompt locally",
            "  note: prompt_toolkit pauses input while remote streams are rendering",
        ]
        for line in lines:
            self._console_print(line)

    def _resolve_pending_collab_id(self, token: str) -> Optional[str]:
        if not self.pending_remote_collabs:
            return None
        if token == "next":
            return next(iter(self.pending_remote_collabs))
        if token in self.pending_remote_collabs:
            return token
        matches = [rid for rid in self.pending_remote_collabs if rid.startswith(token)]
        if len(matches) == 1:
            return matches[0]
        return None

    def _parse_collab_command(self, line: str, command_name: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        # Returns: (peer, prompt, error)
        body = line[len(command_name) :].strip()
        if not body:
            return None, None, "Usage: /collab <peer> <prompt>"

        if self.args.default_peer:
            # Allow '/collab <prompt>' when --default-peer is configured.
            if " " not in body:
                return self.args.default_peer, body, None
            first, rest = body.split(" ", 1)
            # With default peer configured:
            # - '/collab @Peer prompt...' uses explicit peer override
            # - '/collab prompt...' uses default peer
            if first.startswith("@") and rest.strip():
                return first[1:], rest.strip(), None
            return self.args.default_peer, body, None

        parts = body.split(" ", 1)
        if len(parts) < 2 or not parts[1].strip():
            return None, None, "Usage: /collab <peer> <prompt>"
        return parts[0], parts[1].strip(), None

    async def _handle_user_line(self, line: str) -> bool:
        # Returns True if line handled as command and no local turn should run.
        if line in {"/help", "help"}:
            self._print_help()
            self._input_enabled.set()
            return True

        if line == "/context":
            if not self.awareness_history:
                self._console_print(self._muted("[awareness] no updates yet"))
            else:
                self._console_print(self._muted("[awareness] recent:"))
                for item in self.awareness_history:
                    self._console_print(self._muted(f"  - {item}"))
            self._input_enabled.set()
            return True

        if line in {"/pending", self._ux_cmd_collab_pending}:
            if not self.pending_collab_requests:
                self._console_print(self._muted("[collab] no pending outbound requests"))
            else:
                now = time.time()
                self._console_print(self._muted(f"[collab] pending={len(self.pending_collab_requests)}"))
                for request_id, peer in self.pending_collab_requests.items():
                    started = self.pending_collab_started_at.get(request_id, now)
                    elapsed = int(max(0, now - started))
                    self._console_print(self._muted(f"  - id={request_id} to={peer} elapsed={elapsed}s"))
            self._input_enabled.set()
            return True

        if line == "/mode":
            self._console_print("Usage: /mode <open|review|locked>")
            self._input_enabled.set()
            return True

        if line.startswith("/mode "):
            raw_mode = line[len("/mode ") :].strip()
            if raw_mode not in {"open", "review", "review_only", "locked"}:
                self._console_print("Usage: /mode <open|review|locked>")
                self._input_enabled.set()
                return True
            normalized = "review_only" if raw_mode == "review" else raw_mode
            self._set_collab_mode(normalized)
            self._console_print(self._muted(f"[mode] collab_mode={self.collab_mode}"))
            self._persist_state()
            self._input_enabled.set()
            return True

        if line == "/review":
            if not self.pending_remote_collabs:
                self._console_print(self._muted("[review] queue is empty"))
            else:
                self._console_print(self._muted(f"[review] queued={len(self.pending_remote_collabs)}"))
                for req in self.pending_remote_collabs.values():
                    preview = req.prompt.replace("\n", " ")
                    if len(preview) > 100:
                        preview = f"{preview[:97]}..."
                    self._console_print(
                        self._muted(f"  - id={req.request_id} from={req.sender} ts={req.ts} prompt={preview!r}")
                    )
            self._input_enabled.set()
            return True

        if line.startswith("/approve "):
            token = line[len("/approve ") :].strip()
            req_id = self._resolve_pending_collab_id(token)
            if req_id is None:
                self._console_print("Usage: /approve <request_id|next> (id not found or ambiguous)")
                self._input_enabled.set()
                return True
            req = self.pending_remote_collabs.pop(req_id)
            self._refresh_review_queue_state()
            self._persist_state()
            self._console_print(
                self._muted(f"[review] approving request_id={req.request_id} from={req.sender}")
            )
            asyncio.create_task(self._execute_approved_remote_collab(req))
            self._input_enabled.set()
            return True

        if line == "/approve":
            self._console_print("Usage: /approve <request_id|next>")
            self._input_enabled.set()
            return True

        if line.startswith("/reject "):
            token = line[len("/reject ") :].strip()
            req_id = self._resolve_pending_collab_id(token)
            if req_id is None:
                self._console_print("Usage: /reject <request_id|next> (id not found or ambiguous)")
                self._input_enabled.set()
                return True
            req = self.pending_remote_collabs.pop(req_id)
            self._refresh_review_queue_state()
            await self._reject_remote_collab(
                sender=req.sender,
                request_id=req.request_id,
                reason="rejected by operator",
            )
            self._persist_state()
            self._input_enabled.set()
            return True

        if line == "/reject":
            self._console_print("Usage: /reject <request_id|next>")
            self._input_enabled.set()
            return True

        if line == self._ux_cmd_collab or line.startswith(f"{self._ux_cmd_collab} "):
            peer, prompt, err = self._parse_collab_command(line, self._ux_cmd_collab)
            if err:
                self._console_print(err)
                self._input_enabled.set()
                return True
            if len(prompt) > self.args.max_collab_chars:
                self._console_print(
                    f"[collab] prompt too large ({len(prompt)} chars > max {self.args.max_collab_chars})"
                )
                self._input_enabled.set()
                return True

            request_id = str(uuid.uuid4())
            self.pending_collab_requests[request_id] = str(peer)
            self.pending_collab_started_at[request_id] = time.time()
            self._persist_state()
            await self._send_collab(
                {
                    "kind": "collab_request",
                    "to": peer,
                    "request_id": request_id,
                    "prompt": prompt,
                    "ts": int(time.time()),
                }
            )
            self._console_print(self._muted(f"[collab] sent request_id={request_id} to={peer}"), re_prompt=True)
            self._console_print(
                self._muted("[collab] awaiting remote response (use /collab-pending or /pending)"),
                re_prompt=True,
            )
            self.pending_collab_wait_tasks[request_id] = asyncio.create_task(
                self._collab_wait_indicator(request_id, str(peer))
            )
            self._input_enabled.set()
            return True

        if line.startswith("/"):
            self._console_print(f"Unknown command: {line}. Use /help for available commands.")
            self._input_enabled.set()
            return True

        return False

    async def _user_turn_loop(self) -> None:
        while not self._stop_input.is_set():
            line = await self.input_queue.get()

            if line in {"/quit", "/exit"}:
                await self._broadcast_awareness(
                    {
                        "kind": "agent_status",
                        "state": "offline",
                        "agent": self.args.name,
                    }
                )
                self._request_shutdown()
                break

            if line.startswith("/local "):
                line = line[len("/local ") :].strip()
                if not line:
                    self._console_print("Usage: /local <prompt>")
                    self._input_enabled.set()
                    continue

            handled = await self._handle_user_line(line)
            if handled:
                continue

            self._set_pipeline_state("local_user", "processing")
            if self.args.share_local_turns:
                await self._broadcast_awareness(
                    {
                        "kind": "chat_user_prompt",
                        "state": "processing",
                        "prompt": line,
                        "ts": int(time.time()),
                    }
                )

            async with self.turn_lock:
                stream_started = threading.Event()
                stream_emitted = threading.Event()

                def on_local_delta(chunk: str) -> None:
                    with self._console_lock:
                        if not stream_started.is_set():
                            stream_started.set()
                            # Replace spinner line with streaming prefix.
                            if self._status_active:
                                print("\r\033[2K", end="", flush=True)
                                self._status_active = False
                            print("\r\033[2K", end="", flush=True)
                            print(f"{self._role_label('assistant')}> ", end="", flush=True)
                        print(chunk, end="", flush=True)
                    stream_emitted.set()

                indicator_task = asyncio.create_task(self._local_wait_indicator(stop_when=stream_started))
                try:
                    turn_output = await asyncio.to_thread(
                        self._run_turn_blocking,
                        line,
                        "local_user",
                        on_local_delta,
                    )
                except Exception as exc:
                    indicator_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await indicator_task
                    err = str(exc)
                    self._console_print(f"{self._role_label('assistant')}> [{self._role_label('error')}] {err}", re_prompt=True)
                    if self.args.share_local_turns:
                        await self._broadcast_awareness(
                            {
                                "kind": "chat_codex_response",
                                "state": "error",
                                "request_id": str(uuid.uuid4()),
                                "thread_id": self.thread_by_key.get("local_user", ""),
                                "turn_id": "",
                                "status": "failed",
                                "error": err,
                                "prompt": line,
                                "response": "",
                                "ts": int(time.time()),
                            }
                        )
                    self._set_pipeline_state("local_user", "error")
                    self._input_enabled.set()
                    continue
                else:
                    indicator_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await indicator_task
                    if stream_emitted.is_set():
                        # Finish the streamed assistant line.
                        self._console_print("")
                    elif turn_output.response:
                        self._console_print(f"{self._role_label('assistant')}> {turn_output.response}", re_prompt=False)
                    else:
                        self._console_print(f"{self._role_label('assistant')}> [no response]", re_prompt=False)

            next_state = "error" if turn_output.error else "waiting_input"
            if self.args.share_local_turns:
                await self._broadcast_awareness(
                    {
                        "kind": "chat_codex_response",
                        "state": next_state,
                        "request_id": turn_output.request_id,
                        "thread_id": turn_output.thread_id,
                        "turn_id": turn_output.turn_id,
                        "status": turn_output.status,
                        "error": turn_output.error,
                        "prompt": turn_output.prompt,
                        "response": turn_output.response,
                        "ts": int(time.time()),
                    }
                )

            if self.args.share_workspace:
                snapshot = await asyncio.to_thread(self._workspace_summary)
                if snapshot.get("ok"):
                    self.last_workspace_fingerprint = snapshot.get("fingerprint", "")
                    await self._broadcast_awareness(
                        {
                            "kind": "workspace_update",
                            "state": next_state,
                            "workspace_id": snapshot["workspace_id"],
                            "changed_files_count": snapshot["changed_files_count"],
                            "changed_files": snapshot["changed_files"],
                            "ts": int(time.time()),
                        }
                    )

            self._set_pipeline_state("local_user", next_state)
            self._input_enabled.set()

    def run(self) -> None:
        try:
            self.app.start()
            self.app.initialize()
            if self.args.enable_viz:
                selected_port = self._select_viz_port()
                self.viz = ClientFlowVisualizer(title=f"{self.args.name} Graph", port=selected_port)
                self.viz.attach_logger(self.agent.logger)
                self.viz.set_graph_from_dna(json.loads(self.agent.dna()), parse_route=self.flow.parse_route)
                self.viz.start(open_browser=self.args.viz_open_browser)
                if selected_port != int(self.args.viz_port):
                    self._console_print(
                        f"[viz] started on {selected_port} (requested {self.args.viz_port})",
                        re_prompt=False,
                    )
                # Explicit initial state push at startup.
                self._push_viz_states()
            self.agent.run(host=self.args.host, port=self.args.port, config_path=self.args.config)
        finally:
            # Ensure background tasks naturally exit even if Summoner session errors out.
            self._request_shutdown()
            self._persist_state()
            for task in self.pending_collab_wait_tasks.values():
                task.cancel()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactive Summoner + Codex agent with explicit collab/awareness channels."
        )
    )
    parser.add_argument("--name", default="CodexBridgeAgent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--config", default=None, help="Optional Summoner client config JSON path")
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--codex-cwd", default=None, help="Working directory used by Codex app-server")
    parser.add_argument("--workspace-root", default=None, help="Repo path monitored for live code updates")
    parser.add_argument("--workspace-poll-seconds", type=float, default=2.0)
    parser.add_argument("--max-files-per-update", type=int, default=50)
    parser.add_argument("--awareness-history", type=int, default=50)
    parser.add_argument(
        "--show-awareness-live",
        action="store_true",
        help="Print incoming awareness updates live (may interrupt typed input)",
    )
    parser.add_argument(
        "--local-wait-interval",
        type=float,
        default=0.30,
        help="Seconds between local assistant thinking animation frames",
    )
    parser.add_argument("--collab-wait-interval", type=float, default=1.5, help="Seconds between collab waiting status updates")
    parser.add_argument(
        "--collab-wait-notify-seconds",
        type=float,
        default=15.0,
        help="How often to print remote collab wait updates (seconds)",
    )
    parser.add_argument(
        "--collab-max-wait-seconds",
        type=float,
        default=300.0,
        help="Requester-side timeout for pending /collab requests (0 disables timeout).",
    )
    parser.add_argument(
        "--collab-mode",
        choices=["open", "review_only", "locked"],
        default="open",
        help="How inbound remote collab requests are handled",
    )
    parser.add_argument(
        "--allowed-peer",
        action="append",
        default=[],
        help="Allow inbound messages only from this peer (repeatable)",
    )
    parser.add_argument(
        "--max-collab-chars",
        type=int,
        default=4000,
        help="Maximum prompt size for local/remote collab requests",
    )
    parser.add_argument(
        "--max-pending-review-collabs",
        type=int,
        default=20,
        help="Maximum queued remote collab requests while in review_only mode",
    )
    parser.add_argument(
        "--remote-collab-timeout-seconds",
        type=float,
        default=180.0,
        help="Soft timeout threshold for remote collab requests (warn-only; execution continues safely)",
    )
    parser.add_argument(
        "--collab-stream-flush-seconds",
        type=float,
        default=0.08,
        help="Batch window for sending remote collab stream deltas",
    )
    parser.add_argument(
        "--shared-secret",
        default="",
        help="Optional shared secret for message signing/verification",
    )
    parser.add_argument(
        "--sig-ttl-seconds",
        type=int,
        default=300,
        help="Signed message TTL and replay window",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to persisted local state file (defaults to .codex_live_collab_state_<name>.json)",
    )
    parser.add_argument(
        "--ui-backend",
        choices=["auto", "threaded", "prompt_toolkit"],
        default="auto",
        help="Input backend. 'auto' prefers prompt_toolkit when available.",
    )
    parser.add_argument(
        "--history-file",
        default=None,
        help="Input history file (used by prompt_toolkit backend).",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output in terminal",
    )
    parser.add_argument("--default-peer", default=None, help="Default peer for /collab when omitted")
    parser.add_argument("--viz-port", type=int, default=8788, help="Visionary HTTP port")
    parser.add_argument("--no-viz", action="store_true", help="Disable Visionary graph server")
    parser.add_argument("--viz-no-browser", action="store_true", help="Do not auto-open browser for Visionary")
    parser.add_argument(
        "--no-share-local-turns",
        action="store_true",
        help="Do not broadcast local prompt/response awareness events",
    )
    parser.add_argument(
        "--no-share-workspace",
        action="store_true",
        help="Do not broadcast workspace_update events",
    )
    parser.add_argument(
        "--no-accept-remote-collabs",
        action="store_true",
        help="Reject incoming remote collab requests",
    )
    parser.add_argument(
        "--codex-bin",
        default=AppServerConfig().codex_bin,
        help="Path to codex binary. Defaults to bundled SDK binary.",
    )
    args = parser.parse_args()

    args.share_local_turns = not args.no_share_local_turns
    args.share_workspace = not args.no_share_workspace
    args.accept_remote_collabs = not args.no_accept_remote_collabs
    args.enable_viz = not args.no_viz
    args.viz_open_browser = not args.viz_no_browser
    return args


if __name__ == "__main__":
    InteractiveCodexSummonerAgent(parse_args()).run()
