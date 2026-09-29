"""The contract: what a caller may send, and what they are promised back.

These models are the API. Once they are written down, three things become true that were
not true in a notebook: a bad request is rejected before the model is ever called, the
reply has a shape a front end can rely on, and the whole thing documents itself at /docs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, examples=["Where is my order 580638?"])
    thread_id: str | None = Field(
        default=None,
        description="Continue one of your own conversations. Omit to start a new one.",
    )
    customer_id: float | None = Field(
        default=None,
        description="Only read when the service is not checking tokens. With tokens on, the "
                    "token decides who you are, and a different number here is refused.",
        examples=[12381.0],
    )


class Usage(BaseModel):
    """What the request consumed. Present on every successful reply, because a caller who
    cannot see this has no way to know a question cost thirty times what it should."""

    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    seconds: float = 0.0


class Answer(BaseModel):
    thread_id: str
    reply: str
    tools_used: list[str] = []
    usage: Usage = Usage()
    stopped_at_limit: bool = Field(
        default=False,
        description="True when a ceiling ended the run before the agent was finished.",
    )


class ReturnRequest(BaseModel):
    return_id: str
    invoice_no: str
    customer_id: float
    amount: float
    reason: str | None = None
    status: Literal["pending", "approved", "rejected"]
    requested_at: str
    decided_by: str | None = None
    decided_at: str | None = None
    refund_id: str | None = Field(default=None, description="Set once a member of staff has paid it.")
    paid_at: str | None = None


class Order(BaseModel):
    """One of the signed-in customer's orders, as the order desk lists them."""

    invoice_no: str
    placed_at: str
    lines: int
    total: float
    cancelled: bool
    return_request: ReturnRequest | None = Field(
        default=None,
        description="The return request on this order that matters most: a paid one, else the "
                    "latest pending one, else the latest of any.",
    )


class Me(BaseModel):
    """Who a token belongs to."""

    subject: str
    role: Literal["customer", "staff"]
    customer_id: float | None = None


class Refund(BaseModel):
    refund_id: str
    return_id: str
    invoice_no: str
    amount: float
    approved_by: str
    paid_at: str
    replayed: bool = Field(
        default=False,
        description="True when this approval had already been made: nothing was paid twice.",
    )


class ErrorBody(BaseModel):
    """One shape for every failure, so a client writes one error handler instead of five.

    `error` is for code to branch on and never changes wording. `detail` is for a human
    and carries nothing about the inside of the service — no paths, no SQL, no prompt.
    """

    error: Literal[
        "invalid_request",
        "unauthorized",
        "forbidden",
        "thread_not_found",
        "return_not_found",
        "conflict",
        "rate_limited",
        "too_large",
        "over_budget",
        "model_unavailable",
        "timeout",
        "internal",
    ]
    detail: str
    request_id: str


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    model: str
    checks: dict[str, str]


class Version(BaseModel):
    version: str
    prompt_version: str
    canary: str
    model: str
    started_at: str
    demo_sign_in: bool = Field(
        default=False,
        description="True when POST /dev/token hands out tokens: a laptop, never a deployment.",
    )
