"""V3 production bootstrap.

The bootstrap owns the production wiring of the V3 runtime. It is
the only place that may construct ``V3Orchestrator``; callers ask for
a ``V3Application`` and receive an immutable set of cooperating
objects (orchestrator, world model, execution log, tool executor,
policy engine, approval manager).

Security posture:

  * No action is ever auto-approved. The ``ApprovalManager`` is wired
    with an explicit ``approval_provider`` callable (or remains empty
    for tests). Production deployments must inject a provider that
    surfaces approval requests to a human or a real approval backend.
  * No fake ``run_id`` or hard-coded "default" bulk approval is ever
    applied at startup. Bulk approval is the user's opt-in via the
    provider, bound to the real ``run_id`` of the action.
  * Policy, approval, audit, tool, verification, observation and
    execution-log events are all preserved unchanged from the V2
    chain; the bootstrap only orchestrates.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes.capability import create_default_capability_registry
from hermes.execution_log import ExecutionLogStore
from hermes.reasoning.transport import HermesServerReasoningClient
from hermes.runtime.executor import V3Executor
from hermes.runtime.orchestrator import V3Orchestrator
from hermes.security.approval_manager import (
    ApprovalHandler,
    ApprovalManager,
    ParsedApproval,
)
from hermes.security.policy_engine import AuditLogger, PolicyEngine
from hermes.tools.executor import ToolExecutor
from hermes.tools.registry import create_default_registry
from hermes.tools.verifiers.registry import VerifierRegistry, create_default_verifier_registry
from hermes.world_model import WorldModel


# An approval provider is any callable that resolves a
# ``PendingApproval`` into a ``ParsedApproval``. Production deployments
# inject a real provider (UI handler, server-backed approval flow,
# etc.); tests inject their own. A missing provider means the
# ApprovalManager fails every action that requires approval.
ApprovalProvider = Callable[[Any], Awaitable[ParsedApproval]]


@dataclass
class V3Application:
    """Container of the V3 production runtime.

    The bootstrap returns this; callers (the CLI / voice / UI / app
    bootstrap) keep references and pass user input to
    ``orchestrator.process_message``.
    """

    orchestrator: V3Orchestrator
    world_model: WorldModel
    execution_log: ExecutionLogStore
    tool_executor: ToolExecutor
    policy_engine: PolicyEngine
    approval_manager: ApprovalManager
    memory: Any = None
    session_store: Any = None
    _log_dir: Path | None = None

    async def process_message(self, message: str, session_id: str | None = None) -> str:
        active_sid_from_store = None
        if self.session_store is not None:
            try:
                active_sid_from_store = self.session_store.load_active_session_id()
            except Exception:
                active_sid_from_store = None
        if session_id is None:
            if active_sid_from_store:
                session_id = str(active_sid_from_store)
            else:
                import uuid as _u
                session_id = f"sess_{_u.uuid4().hex[:10]}"
        sid = str(session_id)
        current_sid = str(
            getattr(self.memory, "active_session_id", None)
            or self.orchestrator.state.current_session_id
            or ""
        )
        if sid != current_sid and self.world_model is not None:
            try:
                self.world_model.reset()
            except Exception:
                pass
        if self.memory is not None and hasattr(self.memory, "activate_session"):
            try:
                self.memory.activate_session(sid)
            except Exception:
                pass
        loaded = None
        if self.session_store is not None:
            try:
                if sid != current_sid:
                    loaded = self.session_store.load(sid)
                else:
                    existing_session_id = str(self.orchestrator.state.current_session_id or "")
                    if not existing_session_id:
                        if active_sid_from_store and str(active_sid_from_store) == sid:
                            loaded = self.session_store.load(sid)
            except Exception:
                loaded = None
        if loaded is None and self.session_store is not None:
            try:
                orchestrator_sid = str(self.orchestrator.state.current_session_id or "")
                if not orchestrator_sid:
                    if active_sid_from_store and str(active_sid_from_store) == sid:
                        loaded = self.session_store.load(sid)
            except Exception:
                loaded = None
        if loaded is not None:
            try:
                await self.orchestrator.resume_session(loaded)
            except Exception:
                pass
        outcome_reply = await self.orchestrator.process_message(message, session_id=sid)
        try:
            if self.memory is not None and outcome_reply:
                caps = []
                try:
                    ld = getattr(self.orchestrator, "state", None)
                    if ld is not None and ld.last_decision is not None:
                        rc = getattr(ld.last_decision, "required_capabilities", None)
                        if isinstance(rc, (list, tuple)):
                            caps = list(rc)
                except Exception:
                    caps = []
                if hasattr(self.memory, "record_episode") and callable(getattr(self.memory, "record_episode")):
                    try:
                        self.memory.record_episode(
                            summary=str(outcome_reply),
                            capabilities=tuple(caps) if caps else ("system.inspect",),
                        )
                    except Exception:
                        pass
                else:
                    episodic = getattr(self.memory, "episodic", None)
                    if episodic is not None:
                        if hasattr(episodic, "record"):
                            fn = getattr(episodic, "record")
                        elif hasattr(episodic, "record_episode"):
                            fn = getattr(episodic, "record_episode")
                        else:
                            fn = None
                        if fn is not None:
                            try:
                                fn(
                                    summary=str(outcome_reply),
                                    capabilities=tuple(caps) if caps else ("system.inspect",),
                                    outcome="success",
                                )
                            except Exception:
                                try:
                                    fn(
                                        summary=str(outcome_reply),
                                        capabilities=tuple(caps) if caps else ("system.inspect",),
                                    )
                                except Exception:
                                    pass
        except Exception:
            pass
        try:
            sid_current = str(
                getattr(self.memory, "active_session_id", None)
                or self.orchestrator.state.current_session_id
                or sid
            )
            if self._log_dir is not None:
                log_path = Path(str(self._log_dir))
                log_path.mkdir(parents=True, exist_ok=True)
                active_file = log_path / "active_session.json"
                active_file.write_text(
                    '{"session_id": "' + sid_current + '"}',
                    encoding="utf-8",
                )
                sessions_dir = log_path / "sessions"
                sessions_dir.mkdir(parents=True, exist_ok=True)
                sess_file = sessions_dir / f"{sid_current}.json"
                sess_file.write_text(
                    '{"session_id": "' + sid_current + '", "last_summary": '
                    + '"' + (outcome_reply.replace('"', '\\"') or "") + '"}',
                    encoding="utf-8",
                )
                mem_sessions_dir = log_path / "memory" / "sessions"
                mem_sessions_dir.mkdir(parents=True, exist_ok=True)
                mem_sess_file = mem_sessions_dir / f"{sid_current}.json"
                mem_sess_file.write_text(
                    '{"session_id": "' + sid_current + '", "last_summary": '
                    + '"' + (outcome_reply.replace('"', '\\"') or "") + '"}',
                    encoding="utf-8",
                )
                try:
                    snap_state = await self.orchestrator.save_session(sid_current)
                    if self.session_store is not None:
                        self.session_store.save(snap_state)
                        self.session_store.save_active_session_id(sid_current)
                except Exception:
                    pass
        except Exception:
            pass
        return outcome_reply


def build_v3_application(
    *,
    server: Any,
    log_dir: Path | None = None,
    on_status: Callable[[Any, str, dict[str, Any]], Any] | None = None,
    approval_provider: ApprovalProvider | None = None,
    memory: Any = None,
    turn_timeout_seconds: float | None = 120.0,
    fast_path_enabled: bool = False,
) -> V3Application:
    """Construct the V3 application.

    Args:
        server: The Hermes server transport (``HermesServerClient`` in
            production; an httpx mock in tests). The reasoning runtime
            talks to this server; tool execution happens locally.
        log_dir: Where to keep the Execution Log JSONL and the audit
            log. Defaults to ``%LOCALAPPDATA%\\HermesClient\\state\\v3``.
        on_status: Optional status callback (phase, message, extras).
        approval_provider: Optional callable that turns a
            ``PendingApproval`` into a ``ParsedApproval``. **Production
            must inject a real provider.** When omitted the
            ``ApprovalManager`` has no handler and any policy decision
            of ``REQUIRE_APPROVAL`` falls through to ``REJECT``. Tests
            inject a deterministic provider.
        memory: Optional memory facade exposing ``episodic`` and
            ``long_term`` adapters. The runtime consults it read-only
            to enrich the LLM prompt. Defaults to a no-op facade so
            production keeps working without persistent memory.
        fast_path_enabled: Whether the local fast path (rule-matched,
            no-server round-trip) may answer explicit commands like
            "google'u ac". Production defaults to True; tests may
            disable it for deterministic LLM behaviour.
    """
    if log_dir is None:
        from hermes.config.paths import client_state_dir

        log_dir = client_state_dir() / "v3"

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "execution.jsonl"
    log = ExecutionLogStore(path=log_path)
    tools = create_default_registry()
    caps = create_default_capability_registry(tools)
    vers = create_default_verifier_registry()

    policy = PolicyEngine()
    audit = AuditLogger(log_path=str(log_dir / "audit.log"))
    approval = ApprovalManager(audit_logger=audit)
    if approval_provider is not None:
        approval.set_handler(_provider_to_handler(approval_provider))
    # No bulk approval at startup. No "default" run id. The runtime
    # requests approval per-action through the registered provider.

    tool_executor = ToolExecutor(tools, policy, audit, approval, vers)
    reasoning_client = HermesServerReasoningClient(server)
    from hermes.reasoning import ReasoningRuntime

    if memory is None:
        memory_dir = log_dir / "memory"
        from hermes.memory.runtime import RuntimeMemory
        memory = RuntimeMemory(directory=memory_dir)

    executor = V3Executor.from_defaults(
        execution_log=log,
        tool_executor=tool_executor,
        approval_manager=approval,
        tool_registry=tools,
        capability_registry=caps,
        verifier_registry=vers,
    )
    runtime = ReasoningRuntime(
        client=reasoning_client,
        capability_registry=caps,
        execution_log=log,
        memory=memory,
        tool_registry=tools,
        fast_path_enabled=fast_path_enabled,
    )
    orchestrator = V3Orchestrator(
        runtime=runtime,
        executor=executor,
        execution_log=log,
        on_status=on_status,
        turn_timeout_seconds=turn_timeout_seconds,
    )
    session_store = None
    try:
        from hermes.runtime.session import SessionStore
        session_store = SessionStore(directory=log_dir / "sessions")
    except Exception:
        session_store = None
    app = V3Application(
        orchestrator=orchestrator,
        world_model=orchestrator._world_model,
        execution_log=log,
        tool_executor=tool_executor,
        policy_engine=policy,
        approval_manager=approval,
        memory=memory,
        session_store=session_store,
        _log_dir=log_dir,
    )
    return app


def _provider_to_handler(provider: ApprovalProvider) -> ApprovalHandler:
    """Adapt a provider callable into the ``ApprovalHandler`` protocol.

    The provider is awaited; an exception is logged and treated as a
    denial — the security chain fails closed.
    """
    from hermes.utils.logging import get_logger

    logger = get_logger(__name__)

    async def _handler(pending: Any) -> ParsedApproval:
        try:
            return await provider(pending)
        except Exception as exc:  # noqa: BLE001 — fail closed
            logger.error(
                "approval_provider_failed",
                run_id=getattr(pending.request, "run_id", ""),
                action_id=getattr(pending.request, "action_id", ""),
                error=str(exc),
            )
            return ParsedApproval(decision="reject", message=str(exc))

    return _handler


def real_run_id() -> str:
    """Return a fresh correlation id bound to one turn / one mission run.

    Production callers should generate ``run_id`` from this helper so
    every audit, approval, execution-log and recovery event shares the
    same correlation anchor.
    """
    return f"run_{uuid.uuid4().hex[:12]}"


__all__ = [
    "ApprovalProvider",
    "V3Application",
    "build_v3_application",
    "real_run_id",
]