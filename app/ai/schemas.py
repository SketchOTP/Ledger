"""Pydantic schemas for AI structured output."""

from typing import Any

from pydantic import BaseModel, Field


class TopAction(BaseModel):
    title: str
    detail: str = ""


class CfoAiOutput(BaseModel):
    executive_summary: str
    cash_status: str = Field(pattern="^(safe|tight|danger)$")
    top_actions: list[TopAction] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    surplus_strategy: list[str] = Field(default_factory=list)
    debt_strategy: list[str] = Field(default_factory=list)
    research_needed: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


CFO_AI_JSON_SCHEMA: dict[str, Any] = CfoAiOutput.model_json_schema()
