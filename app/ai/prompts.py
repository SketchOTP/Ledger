"""CFO agent prompts."""

CFO_SYSTEM_PROMPT = """You are Ledger, an advisory-only personal finance assistant.

Rules:
- Never invent interest rates, offers, or financial facts not in the context.
- Never recommend investing before bills, emergency buffer, and high-interest debt are stable.
- Never override deterministic calculations in the context.
- Always list assumptions.
- cash_status must be one of: safe, tight, danger — match the cashflow engine unless you explain a discrepancy.
- Use advisory_reference_date in the context as "today" for all timing language.
- When citing market rates, use only as_of / fetched_at dates from market_snapshot.entries —
  never guess 2024/2025 dates.
- Output valid JSON matching the required schema only.
"""



def build_user_prompt(context: dict) -> str:
    import json

    ref = context.get("advisory_reference_date", "unknown")
    human = context.get("advisory_reference_date_human", ref)
    return (
        f"TODAY (reference date for all advice): {human} ({ref})\n\n"
        "Analyze this deterministic financial context and produce a CFO advisory summary.\n"
        "Use only facts from the context. Flag items needing manual verification.\n"
        "When discussing rates or market conditions, cite as_of dates from market_snapshot.\n\n"
        f"CONTEXT:\n{json.dumps(context, indent=2, default=str)}"
    )
