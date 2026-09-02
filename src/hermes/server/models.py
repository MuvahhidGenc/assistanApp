from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RunStatus(StrEnum):
    PENDING = "pending"
    STARTED = "started"
    RUNNING = "running"
    STOPPING = "stopping"
    AWAITING_APPROVAL = "awaiting_approval"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class HermesApprovalChoice(StrEnum):
    """Hermes Agent API approval choices."""

    ONCE = "once"
    SESSION = "session"
    ALWAYS = "always"
    DENY = "deny"


class ApprovalDecision(StrEnum):
    """Local natural-language approval decisions."""

    APPROVE = "approve"
    REJECT = "reject"
    APPROVE_ALL = "approve_all"
    PARTIAL = "partial"
    CANCEL = "cancel"
    CONTINUE = "continue"


class SessionCreate(BaseModel):
    id: str | None = None
    session_id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Session(BaseModel):
    id: str = ""
    session_id: str | None = None
    title: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fill_id(self) -> Session:
        if not self.id and self.session_id:
            self.id = self.session_id
        if not self.session_id and self.id:
            self.session_id = self.id
        return self


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_openai_body(self, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": "user", "content": self.message}],
            "stream": self.stream,
        }


class RunCreate(BaseModel):
    input: str
    session_id: str | None = None
    model: str | None = None
    instructions: str | None = None
    conversation_history: list[dict[str, Any]] | None = None


class ToolCallRequest(BaseModel):
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "read_only"
    execution_target: str = "client"
    mission_id: str | None = None
    step_id: str | None = None


class ApprovalRequest(BaseModel):
    id: str = ""
    run_id: str
    title: str = "Onay gerekli"
    description: str
    tool_name: str
    plan_steps: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class HermesApprovalSubmit(BaseModel):
    run_id: str
    choice: HermesApprovalChoice
    message: str | None = None


class ParsedApproval(BaseModel):
    approval_id: str = ""
    decision: ApprovalDecision
    approved_steps: list[int] | None = None
    excluded_steps: list[int] | None = None
    message: str | None = None


class RunEventType(StrEnum):
    MESSAGE = "message"
    MESSAGE_DELTA = "message.delta"
    ASSISTANT_DELTA = "assistant.delta"
    PLAN = "plan"
    TOOL_CALL = "tool_call"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    APPROVAL_REQUIRED = "approval.request"
    APPROVAL_RESPONDED = "approval.responded"
    STATUS = "status"
    ERROR = "error"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    DONE = "done"


class RunEvent(BaseModel):
    type: RunEventType
    data: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    timestamp: datetime | None = None


class Run(BaseModel):
    id: str = ""
    run_id: str | None = None
    session_id: str | None = None
    status: RunStatus = RunStatus.PENDING
    model: str | None = None
    output: str | None = None
    created_at: datetime | None = None

    @model_validator(mode="after")
    def fill_id(self) -> Run:
        if not self.id and self.run_id:
            self.id = self.run_id
        if not self.run_id and self.id:
            self.run_id = self.id
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class Capabilities(BaseModel):
    object: str | None = None
    platform: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    models: list[str] = Field(default_factory=list)
    version: str = ""
    runtime: str = ""
    endpoints: str = ""


class ModelInfo(BaseModel):
    id: str
    object: str | None = None
    owned_by: str | None = None


class ModelsResponse(BaseModel):
    object: str | None = None
    data: list[ModelInfo] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str = "ok"
    raw: dict[str, Any] = Field(default_factory=dict)


class ToolResultPayload(BaseModel):
    tool_call_id: str
    success: bool
    output: Any = None
    error: str | None = None
