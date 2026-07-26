"""Token usage helpers. Budget is optional — 0 / None means unlimited."""

from __future__ import annotations

from app.models import Job
from app.state_machine import JobStatus


class BudgetExceeded(Exception):
    pass


def is_unlimited(job: Job) -> bool:
    """token_budget <= 0 means user did not set a budget (default)."""
    return int(job.token_budget or 0) <= 0


def add_tokens(job: Job, n: int) -> None:
    job.tokens_used = int(job.tokens_used or 0) + max(0, int(n))
    if is_unlimited(job):
        return
    if job.tokens_used > job.token_budget:
        job.status = JobStatus.BUDGET_EXCEEDED.value
        job.error_message = (
            f"Token budget exceeded: used={job.tokens_used} budget={job.token_budget}"
        )
        raise BudgetExceeded(job.error_message)


def check_budget(job: Job, estimate: int = 0) -> None:
    if is_unlimited(job):
        return
    if int(job.tokens_used or 0) + estimate > job.token_budget:
        job.status = JobStatus.BUDGET_EXCEEDED.value
        job.error_message = (
            f"Token budget would exceed: used={job.tokens_used} "
            f"+est={estimate} budget={job.token_budget}"
        )
        raise BudgetExceeded(job.error_message)


def remaining(job: Job) -> int | None:
    if is_unlimited(job):
        return None
    return max(0, int(job.token_budget) - int(job.tokens_used or 0))
