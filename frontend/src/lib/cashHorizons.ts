/** Liquidity planner horizon keys (match backend LIQUIDITY_HORIZONS). */
export const PROJECTED_HORIZON_OPTIONS = ["30", "60", "90", "180", "6mo"] as const;
export type ProjectedHorizonOption = (typeof PROJECTED_HORIZON_OPTIONS)[number];

export const SAFE_TO_SPEND_HORIZON_OPTIONS = ["180", "6mo"] as const;
export type SafeToSpendHorizonOption = (typeof SAFE_TO_SPEND_HORIZON_OPTIONS)[number];

export function horizonDisplayLabel(h: string): string {
  return h === "6mo" ? "6mo" : `${h}d`;
}

export function getHorizonPayload<T extends { horizon_key?: number | string }>(
  horizons: Record<string, T> | undefined,
  key: string,
): T | undefined {
  if (!horizons) return undefined;
  const direct = horizons[key];
  if (direct) return direct;
  if (key !== "6mo") {
    const alt = horizons[String(Number(key))];
    if (alt) return alt;
  }
  const needle = key === "6mo" ? "6mo" : Number(key);
  return Object.values(horizons).find(
    (row) => row.horizon_key === needle || String(row.horizon_key) === key,
  );
}

export function safeToSpendHint(
  safeHorizon: SafeToSpendHorizonOption,
  active?: { days?: number; flexible_today?: number },
  sameAs180?: boolean,
): string {
  const label = horizonDisplayLabel(safeHorizon);
  const days = active?.days;
  let text = days
    ? `Safe to spend through ${label} (${days} calendar days) — max you can spend today while planned income and obligations in that window still clear.`
    : `Safe to spend through ${label} — max you can spend today while planned income and obligations in that window still clear.`;
  if (sameAs180 && safeHorizon === "6mo") {
    text +=
      " Same amount as 180d for your current schedule — no extra obligations fall between 180d and 6mo.";
  }
  return text;
}

export function projectedBalanceHint(
  horizon: ProjectedHorizonOption,
  active?: { days?: number },
): string {
  const label = horizonDisplayLabel(horizon);
  const days = active?.days;
  if (days) {
    return `Projected checking balance ${label} out (${days} calendar days) if planned income lands and scheduled bills clear. Not all of this is safe to spend.`;
  }
  return `Projected checking balance at ${label} if planned income lands and scheduled bills clear. Not all of this is safe to spend.`;
}
