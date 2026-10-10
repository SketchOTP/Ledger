export type PlaybookStepKind = "in" | "out" | "alert";

export function resolveStepKind(step: {
  step_kind?: PlaybookStepKind;
  action?: string;
}): PlaybookStepKind {
  if (step.step_kind === "in" || step.step_kind === "out" || step.step_kind === "alert") {
    return step.step_kind;
  }
  if (step.action === "shortfall") return "alert";
  if (step.action === "deposit_to_checking" || step.action === "receive_income") {
    return "in";
  }
  return "out";
}

/** Split sign and dollars for timeline UI (+ $2,230 / − $30). */
export function amountDisplayParts(
  kind: PlaybookStepKind,
  amount?: number | null
): { sign: string; value: string } | null {
  if (amount == null || Number.isNaN(Number(amount))) {
    return null;
  }
  const value = `$${Number(amount).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  if (kind === "alert") {
    return { sign: "!", value };
  }
  return { sign: kind === "in" ? "+" : "−", value };
}

/** e.g. −$30 (bill) or +$2,230 (deposit) */
export function signedAmountLabel(kind: PlaybookStepKind, amount?: number | null): string {
  const parts = amountDisplayParts(kind, amount);
  if (!parts) return "—";
  return `${parts.sign}${parts.value}`;
}

export function checkingAfterStep(step: {
  checking_balance?: number | null;
  balance_after?: number | null;
}): number | null {
  const raw = step.checking_balance ?? step.balance_after;
  if (raw == null || Number.isNaN(Number(raw))) return null;
  return Number(raw);
}

export function formatCheckingBalance(amount: number | null | undefined): string {
  if (amount == null || Number.isNaN(Number(amount))) return "—";
  return `$${Number(amount).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/** Timeline direction cue: income/deposit up, payment/shortfall down. */
export function stepDirection(kind: PlaybookStepKind): "up" | "down" {
  return kind === "in" ? "up" : "down";
}
