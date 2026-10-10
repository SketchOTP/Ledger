/** Bounded cash-flow planning horizons supported by the backend plan endpoint. */
export const CASHFLOW_PLAN_HORIZONS = [
  { value: 30, label: "30 days" },
  { value: 60, label: "60 days" },
  { value: 90, label: "90 days" },
  { value: 180, label: "180 days" },
  { value: 182, label: "6 months" },
  { value: 365, label: "1 year" },
  { value: 913, label: "2.5 years" },
  { value: 1826, label: "5 years" },
  { value: 3652, label: "10 years" },
] as const;

export type CashflowPlanHorizon = (typeof CASHFLOW_PLAN_HORIZONS)[number]["value"];

export function cashflowPlanHorizonLabel(horizon: CashflowPlanHorizon): string {
  return CASHFLOW_PLAN_HORIZONS.find((option) => option.value === horizon)?.label ?? `${horizon} days`;
}

export function cashflowPlanBucketDays(horizon: CashflowPlanHorizon): number {
  if (horizon <= 30) return 1;
  if (horizon <= 90) return 3;
  if (horizon <= 180) return 7;
  if (horizon <= 365) return 14;
  if (horizon <= 913) return 30;
  if (horizon <= 1826) return 60;
  return 90;
}
