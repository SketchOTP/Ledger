import { useEffect, useMemo, useState } from "react";
import { Card, Segmented } from "./ui";
import { api, type CashflowPlan } from "../api";
import {
  CASHFLOW_PLAN_HORIZONS,
  cashflowPlanBucketDays,
  cashflowPlanHorizonLabel,
  type CashflowPlanHorizon,
} from "../lib/cashflowPlanHorizons";
import { projectReserveLines, type ReserveSnapshot } from "../lib/reserveProjection";

interface Bucket {
  date: string;
  label: string;
  income: number;
  outflow: number;
  balance: number;
}

const MONTHLY_HORIZON_SLOTS: Partial<Record<CashflowPlanHorizon, number>> = {
  182: 6,
  365: 12,
  913: 30,
  1826: 60,
  3652: 120,
};

function formatDateLabel(dateString: string, horizon: CashflowPlanHorizon): string {
  const date = new Date(`${dateString}T00:00:00`);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const year = String(date.getFullYear()).slice(-2);
  if (horizon >= 1826) return String(date.getFullYear());
  if (horizon > 180) return `${month}-${year}`;
  return `${month}-${day}-${year}`;
}

function monthlySlots(plan: CashflowPlan, horizon: CashflowPlanHorizon): Bucket[] {
  const series = plan.daily_series || [];
  if (!series.length) return [];
  const slotCount = MONTHLY_HORIZON_SLOTS[horizon];
  if (!slotCount) return [];

  const firstDate = new Date(`${series[0].date}T00:00:00`);
  const firstMonth = new Date(firstDate.getFullYear(), firstDate.getMonth(), 1);
  return Array.from({ length: slotCount }, (_, index) => {
    const monthStart = new Date(firstMonth.getFullYear(), firstMonth.getMonth() + index, 1);
    const year = monthStart.getFullYear();
    const month = monthStart.getMonth();
    const monthRows = series.filter((row) => {
      const date = new Date(`${row.date}T00:00:00`);
      return date.getFullYear() === year && date.getMonth() === month;
    });
    const last = monthRows[monthRows.length - 1] || series[series.length - 1];
    return {
      date: last.date,
      label: formatDateLabel(last.date, horizon),
      income: monthRows.reduce((sum, row) => sum + row.income, 0),
      outflow: monthRows.reduce((sum, row) => sum + row.outflow, 0),
      balance: last.balance,
    };
  });
}

function aggregateSeries(plan: CashflowPlan, bucketDays: number, horizon: CashflowPlanHorizon): Bucket[] {
  const monthly = monthlySlots(plan, horizon);
  if (monthly.length) return monthly;
  const series = plan.daily_series || [];
  if (!series.length) return [];

  const buckets: Bucket[] = [];
  for (let i = 0; i < series.length; i += bucketDays) {
    const slice = series.slice(i, i + bucketDays);
    if (!slice.length) continue;
    const income = slice.reduce((s, d) => s + d.income, 0);
    const outflow = slice.reduce((s, d) => s + d.outflow, 0);
    const last = slice[slice.length - 1];
    const startLabel = formatDateLabel(slice[0].date, horizon);
    buckets.push({
      date: last.date,
      label: startLabel,
      income,
      outflow,
      balance: last.balance,
    });
  }
  return buckets;
}

function formatMoney(n: number): string {
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${Math.round(n)}`;
}

export default function MoneyCashflowChart({ refreshKey, compact = false, reserveSnapshot }: {
  refreshKey: number;
  compact?: boolean;
  reserveSnapshot?: ReserveSnapshot;
}) {
  const [horizon, setHorizon] = useState<CashflowPlanHorizon>(182);
  const [plan, setPlan] = useState<CashflowPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .cashflowPlan(horizon)
      .then(setPlan)
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load forecast"))
      .finally(() => setLoading(false));
  }, [horizon, refreshKey]);

  const bucketDays = cashflowPlanBucketDays(horizon);
  const buckets = useMemo(
    () => (plan ? aggregateSeries(plan, bucketDays, horizon) : []),
    [plan, bucketDays, horizon]
  );
  const reserveDays = useMemo(
    () => plan && reserveSnapshot ? projectReserveLines(plan.daily_series, reserveSnapshot) : [],
    [plan, reserveSnapshot],
  );
  const reserveByDate = useMemo(
    () => new Map(reserveDays.map((point) => [point.date, point])),
    [reserveDays],
  );
  const reserveBuckets = useMemo(
    () => buckets.map((bucket) => reserveByDate.get(bucket.date)),
    [buckets, reserveByDate],
  );

  const maxBar = Math.max(1, ...buckets.map((b) => Math.max(b.income, b.outflow)));
  const reserveFields: Array<"cushion" | "surprises" | "uncommitted" | "extraSpend"> = reserveSnapshot
    ? [
      ...(reserveSnapshot.cushionCurrent != null && reserveSnapshot.cushionGoal > 0 ? ["cushion" as const] : []),
      ...(reserveSnapshot.extraSpendNow != null ? ["extraSpend" as const] : []),
    ] : [];
  const balances = buckets.map((b, index) => reserveBuckets[index]?.balanceAfterSpending ?? b.balance);
  const lineValues = reserveBuckets.flatMap((point) => point
    ? reserveFields.map((field) => point[field]).filter((value): value is number => value != null)
    : []);
  const minBal = Math.min(0, ...balances, ...lineValues);
  const maxBal = Math.max(1, ...balances, ...lineValues);
  const balRange = maxBal - minBal || 1;

  const chartW = 640;
  const chartH = 200;
  const padL = 56;
  const padR = 12;
  const padT = 16;
  const padB = 28;
  const innerH = chartH - padT - padB;
  const innerW = chartW - padL - padR;
  const barW = buckets.length ? innerW / buckets.length : innerW;
  const barGap = Math.min(2, barW * 0.12);
  const singleBarW = Math.max(0.75, (barW - barGap * 2) / 2);

  const lineY = (value: number) => padT + innerH - ((value - minBal) / balRange) * innerH;
  // Keep exact $100 marks when they are distinguishable. Wider ranges need
  // explicitly labelled larger steps rather than an unreadable solid stripe.
  const hundredSpacing = innerH * 100 / balRange;
  const minimumLongRangeStep = balRange * 6 / innerH;
  const axisStep = hundredSpacing >= 3.5 ? 100
    : [200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000]
      .find((step) => step >= minimumLongRangeStep) ??
      Math.ceil(minimumLongRangeStep / 100_000) * 100_000;
  const firstTick = Math.ceil(minBal / axisStep);
  const lastTick = Math.floor(maxBal / axisStep);
  const axisTicks = Array.from(
    { length: Math.max(0, lastTick - firstTick + 1) },
    (_, index) => (firstTick + index) * axisStep,
  ).filter((value) => value > minBal && value < maxBal);
  const axisTickPath = axisTicks
    .map((value) => `M ${padL - 9} ${lineY(value)} h 6`)
    .join(" ");
  const balancePoints = balances
    .map((balance, i) => {
      const x = padL + i * barW + barW / 2;
      return `${x},${lineY(balance)}`;
    })
    .join(" ");
  const reservePoints = (field: "cushion" | "surprises" | "uncommitted" | "extraSpend") => reserveBuckets
    .map((point, i) => {
      const value = point?.[field];
      if (value == null) return null;
      const x = padL + i * barW + barW / 2;
      return `${x},${lineY(value)}`;
    })
    .filter((point): point is string => point != null)
    .join(" ");
  const cushionPoints = reserveFields.includes("cushion") ? reservePoints("cushion") : "";
  const surprisePoints = reserveFields.includes("surprises") ? reservePoints("surprises") : "";
  const uncommittedPoints = reserveFields.includes("uncommitted") ? reservePoints("uncommitted") : "";
  const extraSpendPoints = reserveFields.includes("extraSpend") ? reservePoints("extraSpend") : "";
  const goal = reserveSnapshot?.cushionGoal ?? 0;

  const hasData = buckets.length > 0 && (
    buckets.some((b) => b.income > 0 || b.outflow > 0) || reserveDays.length > 0
  );

  return (
    <Card className={`money-chart-card${compact ? " dashboard-chart" : ""}`}>
      <div className="money-chart-header">
        <div>
            <h3>{compact ? "Money over time" : "Cash forecast"}</h3>
            {!compact && <p className="field-hint" style={{ margin: 0 }}>
            Income vs bills, debt &amp; mortgage — projected balance line
            </p>}
        </div>
      </div>

      <Segmented
        value={String(horizon)}
        onChange={(v) => setHorizon(Number(v) as CashflowPlanHorizon)}
        options={CASHFLOW_PLAN_HORIZONS.filter(option => !reserveSnapshot?.allowanceProjection || option.value <= 182).map((option) => ({
          value: String(option.value),
          label: option.label,
        }))}
      />

      {loading && <p className="subtitle">Loading chart…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && !hasData && (
        <p className="subtitle" style={{ padding: "1rem 0" }}>
          Add a paycheck and bills with due dates to see your forecast chart.
        </p>
      )}

      {!loading && !error && hasData && plan && (
        <>
          <div className="chart-legend">
            <span>
              <i className="dot income" /> Income
            </span>
            <span>
              <i className="dot outflow" /> {compact ? "Bills" : "Bills & payments"}
            </span>
            <span>
              <i className="dot balance" /> Balance
            </span>
            {cushionPoints && <span><i className="dot cushion" /> {compact ? "Safety net" : "Emergency cushion"}</span>}
            {surprisePoints && <span><i className="dot surprises" /> Near-term surprise reserve</span>}
            {uncommittedPoints && <span><i className="dot uncommitted" /> Projected uncommitted</span>}
            {extraSpendPoints && <span><i className="dot extra-spend" /> {compact ? "Allowance" : "Two-week allowance remaining"}</span>}
          </div>

          <div className="chart-scroll">
            <svg
              viewBox={`0 0 ${chartW} ${chartH}`}
              className="money-chart-svg"
              role="img"
              aria-label={`Cash forecast for ${cashflowPlanHorizonLabel(horizon)}. Each short left-axis mark represents $${axisStep.toLocaleString()}.${reserveDays.length ? " Balance after the plan, emergency fund and two-week allowance share one scale" : ""}`}
            >
              <path
                d={axisTickPath}
                className="money-axis-ticks"
                data-dollar-step={axisStep}
                fill="none"
                stroke="var(--muted)"
                strokeWidth={0.75}
              />
              {/* zero line if negative territory */}
              {minBal < 0 && (
                <line
                  x1={padL}
                  x2={chartW - padR}
                  y1={lineY(0)}
                  y2={lineY(0)}
                  stroke="var(--border)"
                  strokeDasharray="4 4"
                />
              )}

              {buckets.map((b, i) => {
                const x0 = padL + i * barW + barGap;
                const incomeH = (b.income / maxBar) * (innerH * 0.45);
                const outH = (b.outflow / maxBar) * (innerH * 0.45);
                const baseY = padT + innerH * 0.55;
                return (
                  <g key={i}>
                    <rect
                      x={x0}
                      y={baseY - incomeH}
                      width={singleBarW}
                      height={Math.max(1, incomeH)}
                      rx={2}
                      className="bar-income"
                    />
                    <rect
                      x={x0 + singleBarW + barGap}
                      y={baseY - outH}
                      width={singleBarW}
                      height={Math.max(1, outH)}
                      rx={2}
                      className="bar-outflow"
                    />
                  </g>
                );
              })}

              {buckets.length > 1 && (
                <polyline
                  points={balancePoints}
                  fill="none"
                  className="line-balance"
                  strokeWidth={2.5}
                  strokeLinejoin="round"
                />
              )}
              {reserveFields.map((field) => {
                const points = field === "cushion" ? cushionPoints : field === "surprises" ? surprisePoints : field === "uncommitted" ? uncommittedPoints : extraSpendPoints;
                return <g key={field}>
                  {points && <polyline points={points} fill="none" className={`line-${field === "extraSpend" ? "extra-spend" : field}`} strokeWidth={2.4} strokeLinejoin="round" />}
                </g>;
              })}

              {buckets.map((b, i) => {
                if (i % Math.ceil(buckets.length / 8) !== 0 && i !== buckets.length - 1) return null;
                const x = padL + i * barW + barW / 2;
                return (
                  <text key={`t-${i}`} x={x} y={chartH - 6} textAnchor="middle" className="chart-axis-label">
                    {b.label}
                  </text>
                );
              })}

              <text x={8} y={padT + 8} className="chart-axis-label">
                {formatMoney(maxBal)}
              </text>
              <text x={8} y={padT + innerH} className="chart-axis-label">
                {formatMoney(minBal)}
              </text>
            </svg>
          </div>
          <details className="dashboard-chart-notes"><summary>Chart details</summary><p className="field-hint money-chart-axis-note">Left marks: ${axisStep.toLocaleString()} each.</p>

          {reserveSnapshot && <p className="field-hint money-chart-reserve-note">
            All lines use the same dollar scale. These amounts are within your cash, not extra accounts or automatic transfers.
            {reserveSnapshot.allowanceProjection ? " The balance includes bills, daily needs, groceries, and allowance releases. The safety net grows with planned monthly contributions until its goal. Future allowances are forecast amounts, not spendable now. Allowance projections stop at the six-month planning boundary." : ` The emergency cushion protects up to ${formatMoney(goal)} once. The balance includes dated bills and daily essentials through the six-month plan, then uses average essentials for longer chart views. Two-week allowance remaining is the remaining dated headroom after the cushion; projected amounts are not additional money to add together.`}
          </p>}

          </details>

          {!compact && <div className="chart-summary-row">
            <div>
              <span className="chart-summary-label">End balance</span>
              <strong>${plan.projected_balance_end.toLocaleString()}</strong>
            </div>
            <div>
              <span className="chart-summary-label">Lowest</span>
              <strong>
                ${plan.lowest_balance.balance.toLocaleString()} · {plan.lowest_balance.date}
              </strong>
            </div>
            <div>
              <span className="chart-summary-label">Monthly total</span>
              <strong>
                ${(plan.monthly_obligations?.total ?? 0).toLocaleString()}
              </strong>
            </div>
            <div>
              <span className="chart-summary-label">Bills / debt / cards / mortgage</span>
              <strong>
                ${(plan.monthly_bills_estimate ?? 0).toLocaleString()} /{" "}
                ${(plan.monthly_debt_minimums ?? 0).toLocaleString()} /{" "}
                ${(plan.monthly_obligations?.credit_card_minimums ?? 0).toLocaleString()} /{" "}
                ${(plan.monthly_mortgage_payments ?? 0).toLocaleString()}
              </strong>
            </div>
          </div>}
        </>
      )}
    </Card>
  );
}
