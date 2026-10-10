/**
 * Consumer-facing copy for the household surfaces.
 *
 * The API is intentionally allowed to use precise internal vocabulary.  This
 * boundary keeps that vocabulary out of the default Today, Money, and Moves
 * experience while leaving the raw detail available behind an explicit
 * disclosure when it is useful.
 */

const INTERNAL_TERM_REPLACEMENTS: Array<[RegExp, string]> = [
  [/\bNEEDS_ECONOMICS\b/gi, "We need one more detail before we can tell if this is worth doing."],
  [/\bNEEDS_EVIDENCE\b/gi, "We need a current offer or quote."],
  [/\bDECISION_GRADE\b/gi, "ready to consider"],
  [/\bqualification(?:_status)?\b/gi, "readiness"],
  [/\blifecycle(?:_status)?\b/gi, "progress"],
  [/\brank(?:ing)?(?: score)?\b/gi, "how it was compared"],
  [/\bevidence freshness\b/gi, "how recently this was checked"],
  [/\bdeployable surplus\b/gi, "safe-to-use money"],
  [/\bexperimental capital\b/gi, "money set aside for trying something new"],
  [/\bexpected net\b/gi, "possible gain after known costs"],
  [/\bcapital at risk\b/gi, "money you could lose"],
  [/\bliquidity lockup\b/gi, "time your money would be unavailable"],
  [/\bcapability fit\b/gi, "fit for you"],
  [/\bscalability\b/gi, "room to grow"],
  [/\bautomation potential\b/gi, "how much can run automatically"],
  [/\breversibility\b/gi, "how easy it is to undo"],
  [/\bcomplexity\b/gi, "how complicated it is"],
  [/\bverification status\b/gi, "whether the details were checked"],
  [/\bfreshness status\b/gi, "how recently the details were checked"],
  [/\bresearch run\b|\bprovider\b/gi, "source"],
  [/\bconstraining horizon\b/gi, "the tightest point ahead"],
  [/\bsafety gate\b|\bevidence gate\b/gi, "current checks"],
  [/\brejection reason\b/gi, "why this is not a fit right now"],
  [/\bexternal evidence not verified(?: fresh)?\b/gi, "We need a current offer or quote."],
  [/\bprobability unknown\b/gi, "We do not know the likelihood yet."],
  [/\bexpected economics unknown\b|\beconomics unknown\b/gi, "We need one more detail before we can tell if this is worth doing."],
];

export function plainText(value: unknown, fallback = "") : string {
  const text = String(value ?? fallback).trim();
  return INTERNAL_TERM_REPLACEMENTS.reduce((current, [pattern, replacement]) => current.replace(pattern, replacement), text);
}

export function todayWarning(value: unknown): string | null {
  const raw = String(value ?? "").trim();
  if (!raw) return null;
  const normalized = raw.toLowerCase();
  if (normalized.includes("paycheck") && (normalized.includes("missing") || normalized.includes("recurrence") || normalized.includes("date"))) {
    return "We need your next payday to look ahead accurately.";
  }
  if (normalized.includes("below uncertainty") || normalized.includes("minimum projected") || normalized.includes("tight")) {
    return "Money may get tight in the next few months.";
  }
  if (normalized.includes("past due") || normalized.includes("overdue")) {
    return "A payment may need your attention.";
  }
  if (normalized.includes("no income") || normalized.includes("income missing")) {
    return "Add your next income date so Ledger can look ahead accurately.";
  }
  return "Something in your plan needs attention.";
}

export function horizonText(riskLevel: string | undefined, horizon: unknown): string {
  if (riskLevel === "safe") return "Your plan stays covered for the period we're looking ahead.";
  if (typeof horizon === "string" && horizon.trim()) {
    const raw = horizon.trim();
    if (/^\d+$/.test(raw)) return `The tightest point is within the next ${horizonPhrase(Number(raw))}.`;
    return `The tightest point is around ${raw}.`;
  }
  if (typeof horizon === "number" && Number.isFinite(horizon)) return `The tightest point is within the next ${horizonPhrase(horizon)}.`;
  return "Review the forecast details before making a large change.";
}

function horizonPhrase(days: number): string {
  if (days <= 31) return "month";
  if (days <=  ninetyDays) return "few months";
  if (days <= 180) return "six months";
  return "year";
}

const ninetyDays = 90;

export function candidateWhyNow(value: unknown, missingFacts: unknown[] = []): string {
  const raw = String(value ?? "").trim();
  const lower = raw.toLowerCase();
  if (missingFacts.length || lower.includes("validation") || lower.includes("missing") || lower.includes("unknown")) {
    return "We need one more detail before we can tell if this is worth doing.";
  }
  if (lower.includes("expires") || lower.includes("soon")) return "This detail may change soon, so it is worth checking now.";
  if (lower.includes("apr") || lower.includes("rate") || lower.includes("benchmark")) return "Your current rate may be higher than a current alternative.";
  if (lower.includes("recurring") || lower.includes("charge") || lower.includes("subscription")) return "A recurring charge may be worth reviewing.";
  if (lower.includes("demand") || lower.includes("skill") || lower.includes("listing")) return "This may match something you are able and willing to do.";
  return "This may help improve your finances.";
}

export function candidateNextStep(value: unknown, missingFacts: unknown[] = []): string {
  const raw = String(value ?? "").trim();
  const lower = raw.toLowerCase();
  if (lower.includes("resolve next") || lower.includes("missing fact") || lower.includes("complete economics")) {
    return "We need one more detail before we can tell if this is worth doing.";
  }
  if (lower.includes("current source") || lower.includes("quote") || lower.includes("verify")) {
    return "Get a current offer or quote.";
  }
  if (lower.includes("payday") || lower.includes("pay date")) return "Add your next payday so Ledger can look ahead.";
  if (lower.includes("cancel") || lower.includes("downgrade")) return "Check whether this recurring charge can be canceled or lowered.";
  if (lower.includes("experiment") || lower.includes("validate") || lower.includes("demand")) return "Try one small test before committing more time or money.";
  if (lower.includes("accepted") || lower.includes("owner action") || lower.includes("in progress")) return "Complete the next action shown here.";
  if (missingFacts.length) return "We need one more detail before we can tell if this is worth doing.";
  return "Review this idea and choose the smallest next step.";
}

export function movesEmptyState(view: "best" | "active" | "watching" | "history"): string {
  if (view === "best") return "Nothing worth changing right now.";
  if (view === "active") return "You don't have anything in progress.";
  if (view === "watching") return "No useful ideas need your attention right now.";
  return "No past moves yet.";
}

export function movesPurpose(): string {
  return "Moves turns Ledger's financial picture into clear actions that could improve your position.";
}

export function movesViewDescription(view: "best" | "active" | "watching" | "history"): string {
  if (view === "best") return "Ready-to-do actions that look worthwhile and safe right now.";
  if (view === "active") return "Actions you have chosen and are currently working through.";
  if (view === "watching") return "Ideas Ledger is still checking or waiting for one useful detail about.";
  return "Actions you finished, dismissed, or chose not to continue.";
}

export function movesEmptyExplanation(view: "best" | "active" | "watching" | "history", ideaCount = 0): string {
  if (view === "best" && ideaCount > 0) {
    return "Ledger is checking the ideas under Ideas. It will only place something here when the benefit is clear, it fits your finances, and there is a sensible next step.";
  }
  if (view === "best") return "Ledger will keep looking as your bills, income, and spending change. It will not fill this list with guesses.";
  return movesViewDescription(view);
}

const MONEY_FIELD_LABELS: Record<string, string> = {
  next_pay_date: "Add your next payday so Ledger can look ahead.",
  amount: "Add the amount so Ledger can include it in your plan.",
  due_date: "Add a due date so Ledger can plan for it.",
  balance: "Add the current balance so Ledger can include it in your plan.",
};

export function moneyGuidance(sectionId: string, missingFields: string[]): string | null {
  if (!missingFields.length) return null;
  const first = missingFields.map((field) => MONEY_FIELD_LABELS[field] || "Add the missing detail so Ledger can include it in your plan.")[0];
  if (sectionId === "income" && missingFields.includes("next_pay_date")) return MONEY_FIELD_LABELS.next_pay_date;
  return first || null;
}

export function plainSectionSummary(sectionId: string, summary: string): string {
  if (sectionId === "debts" || sectionId === "mortgage" || sectionId === "credit_cards") {
    return "Ledger already includes these payments in your plan.";
  }
  return plainText(summary);
}
