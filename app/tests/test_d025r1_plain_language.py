from pathlib import Path

ROOT = Path(__file__).parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_plain_language_boundary_covers_internal_terms() -> None:
    source = read("frontend/src/lib/plainLanguage.ts")
    for term in (
        "NEEDS_ECONOMICS",
        "NEEDS_EVIDENCE",
        "DECISION_GRADE",
        "deployable surplus",
        "experimental capital",
        "constraining horizon",
        "safety gate",
        "evidence gate",
        "expected net",
        "capital at risk",
        "liquidity lockup",
    ):
        assert term in source
    assert "export function todayWarning" in source
    assert "export function movesEmptyState" in source
    assert "export function moneyGuidance" in source


def test_default_surfaces_do_not_render_known_internal_copy() -> None:
    today = read("frontend/src/pages/Today.tsx")
    moves = read("frontend/src/pages/Moves.tsx")
    money = read("frontend/src/pages/MyMoney.tsx")

    prohibited_default_copy = (
        "Only candidates that pass the current safety and evidence gates can appear here.",
        "Nothing qualifies for this view yet.",
        "LedgerMind does not pad the list.",
        "Missing:",
        "rechecked the economics and safety gates",
        "status}>",
    )
    for phrase in prohibited_default_copy:
        assert phrase not in today
        assert phrase not in moves
        assert phrase not in money

    assert "candidateWhyNow" in moves
    assert "candidateNextStep" in moves
    assert "todayWarning" in today
    assert "MoneyCashflowChart" in today
    assert "compact" in today
    assert "moneyGuidance" in money
    assert "plainSectionSummary" in money

    # Backend-generated decision text must cross the presentation boundary.
    assert "<p>{brief.why_now}</p>" not in moves
    assert "<p className=\"decision-next\">{brief.next_step}</p>" not in moves
    assert "candidate.rejection_reasons.join" not in moves


def test_novice_empty_states_and_responsive_quick_view_contract_are_present() -> None:
    moves = read("frontend/src/pages/Moves.tsx")
    plain = read("frontend/src/lib/plainLanguage.ts")
    styles = read("frontend/src/styles.css")

    for phrase in (
        "Nothing worth changing right now.",
        "You don't have anything in progress.",
        "No useful ideas need your attention right now.",
        "No past moves yet.",
        "We need one more detail before we can tell if this is worth doing.",
    ):
        assert phrase in plain or phrase in moves

    assert "Free to use now" in read("frontend/src/pages/Today.tsx")
    assert "today-money-grid" in styles
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in styles
    assert "details" in read("frontend/src/pages/Today.tsx")
    assert "details" in moves


def test_moves_explains_purpose_and_empty_best_state() -> None:
    plain = read("frontend/src/lib/plainLanguage.ts")
    moves = read("frontend/src/pages/Moves.tsx")
    assert "movesPurpose" in plain
    assert "movesViewDescription" in plain
    assert "movesEmptyExplanation" in plain
    assert "What Moves is for" in moves
    assert "See ideas being checked" in moves


def test_optional_household_objects_are_relevance_filtered() -> None:
    moves = read("frontend/src/pages/Moves.tsx")
    assert "NOT_CURRENTLY_APPLICABLE" in moves
    assert "PREREQUISITE_MISSING" in moves
    assert "cash_yield_benchmark" in moves
    assert "savings" in moves


def test_cashflow_chart_exposes_bounded_long_range_outlooks() -> None:
    chart = read("frontend/src/components/MoneyCashflowChart.tsx")
    horizons = read("frontend/src/lib/cashflowPlanHorizons.ts")
    for label in ("6 months", "1 year", "2.5 years", "5 years", "10 years"):
        assert label in chart or label in horizons
    assert "3652" in horizons
    assert "cashflowPlanBucketDays" in chart
