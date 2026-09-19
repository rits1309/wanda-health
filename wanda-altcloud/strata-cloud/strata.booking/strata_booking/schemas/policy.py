"""Pydantic contracts for cancellation policies."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PolicyBody(BaseModel):
    scope: Literal["programme", "coach"]
    programme_id: str
    coach_id: str | None = None
    cancellation_window_hours: int = Field(ge=0, le=24 * 30)
    cancellation_allowed_by: Literal["member", "coach", "both"]

    @model_validator(mode="after")
    def _coach_scope_needs_coach(self) -> "PolicyBody":
        if self.scope == "coach" and self.coach_id is None:
            raise ValueError("coach_id is required for a coach-scoped policy")
        if self.scope == "programme" and self.coach_id is not None:
            raise ValueError("coach_id is not allowed on a programme-scoped policy")
        return self


class PolicyOut(BaseModel):
    id: str
    scope: str
    programme_id: str
    coach_id: str | None
    cancellation_window_hours: int
    cancellation_allowed_by: str
    version: int
    active: bool


class EffectivePolicyOut(BaseModel):
    """The policy that actually governs a coach in a programme (coach override > programme)."""

    source: Literal["coach", "programme", "default"]
    cancellation_window_hours: int
    cancellation_allowed_by: str


class CancelBody(BaseModel):
    reason: str | None = None
