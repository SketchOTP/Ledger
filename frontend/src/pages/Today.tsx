import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type PlannedSpendingAttention, type V2TodayPayload } from "../api";
import { Btn, Card, IconBtn, IconCheckCircle, PageHeader, Pill } from "../components/ui";
import { CashProfileForm } from "../components/money/CashProfileForm";
import MoneyCashflowChart from "../components/MoneyCashflowChart";
import FinancialAssistant from "../components/FinancialAssistant";
import { isPayable, markObligationPaid, payableKey } from "../lib/markObligationPaid";
import { isReceivable, markIncomeReceived, receivableKey } from "../lib/markIncomeReceived";
import { todayWarning } from "../lib/plainLanguage";

const currency = (value: number | null | undefined) =>
  value == null ? "Unknown" : `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
const exactMoney = (value: number | null | undefined) =>
  value == null ? "Unknown" : `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default function Today() {
  const [today, setToday] = useState<V2TodayPayload | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [optionalPurchase, setOptionalPurchase] = useState("");
  const loading = useRef(false);
  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      setToday(await api.v2Today());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load today");
    } finally { loading.current = false; }
  }, []);
  useEffect(() => {
    void load();
    const refreshVisible = () => { if (document.visibilityState === "visible") void load(); };
    const timer = window.setInterval(refreshVisible, 60_000);
    window.addEventListener("focus", refreshVisible);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshVisible);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [load]);

  const refreshFinancial = async () => {
    setBusy(true);
    try { await load(); } finally { setBusy(false); }
  };
  const mark = async (row: V2TodayPayload["immediate_obligations"][number]) => {
    const key = row.kind === "income" ? receivableKey(row) : payableKey(row);
    setBusy(true);
    try {
      if (row.kind === "income" && isReceivable(row)) await markIncomeReceived(row);
      if (row.kind !== "income" && isPayable(row)) await markObligationPaid(row);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : `Could not update ${key}`); }
    finally { setBusy(false); }
  };
  const recordSpending = async (item: PlannedSpendingAttention) => {
    setBusy(true);
    try {
      await api.recordPlannedSpending({
        budget_category_id: item.category_id,
        planned_date: item.planned_date,
        transaction_date: localDate(),
        amount: item.planned_amount,
      });
      setError("");
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not record this purchase."); }
    finally { setBusy(false); }
  };

  const recordOptionalPurchase = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const amount = Number(optionalPurchase);
    if (!Number.isFinite(amount) || amount <= 0) return;
    setBusy(true);
    try {
      await api.createTransaction({amount, category: "Discretionary", description: "Allowance purchase", transaction_date: localDate()});
      setOptionalPurchase("");
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not record the allowance purchase."); }
    finally { setBusy(false); }
  };

  const reserveSnapshot = useMemo(() => {
    const financial = today?.financial_state;
    const breakdown = financial?.today_breakdown;
    const paymentPlan = financial?.payment_plan;
    const rescue = financial?.cash_rescue;
    if (!breakdown || !paymentPlan) return undefined;
    const verified = !error && !today?.checking_activity_pending && !!rescue?.normal_spending_entered && !!rescue?.cushion_configured &&
      !rescue.first_gap &&
      breakdown.coverage_status !== "unknown" && breakdown.coverage_status !== "shortfall" && breakdown.coverage_status !== "savings_shortfall";
    return {
      allowanceProjection: financial?.allowance?.projection,
      cushionCurrent: financial?.allowance?.emergency_current ?? breakdown.cash_cushion_goal ?? 0,
      cushionGoal: breakdown.cash_cushion_goal ?? 0,
      suggestedRefill30Days: breakdown.cash_cushion_refill ?? 0,
      nearTermSurprises: 0,
      uncommittedNow: verified ? breakdown.flexible_safety_remaining ?? null : null,
      extraSpendNow: verified ? breakdown.discretionary_spending_30_days ?? null : null,
      extraSpendShare: breakdown.discretionary_share ?? 0,
      usualSpending30Days: paymentPlan.rows
        .filter((row) => row.category === "living")
        .map((row) => ({ date: row.date, amount: row.amount })),
    };
  }, [today, error]);

  if (!today && !error) return <p className="subtitle">Loading your financial picture…</p>;
  const money = today?.financial_state;
  const breakdown = money?.today_breakdown;
  const allowance = money?.allowance;
  const groceryTotal = money?.payment_plan?.rows.filter(row => row.category === "living")
    .reduce((total, row) => total + (row.spending_kind === "groceries" || row.name.toLowerCase().includes("grocer")
      ? row.amount : row.pending_grocery_amount ?? 0), 0) ?? 0;
  const rescue = money?.cash_rescue;
  const paymentPlan = money?.payment_plan;
  const paymentSteps = paymentPlan?.rows.filter((row) => row.category !== "living" || row.spending_kind === "groceries" || row.spending_kind === "recorded_pending") || [];
  const optionalVerified = !error && !today?.checking_activity_pending && !!rescue?.normal_spending_entered && !!rescue?.cushion_configured &&
    !rescue.first_gap && breakdown?.coverage_status !== "unknown" &&
    breakdown?.coverage_status !== "shortfall" && breakdown?.coverage_status !== "savings_shortfall";
  const tone = rescue?.first_gap ? "danger" : rescue && !rescue.normal_spending_entered ? "tight" : today?.safety_status === "safe" && optionalVerified ? "safe" : today?.safety_status === "at_risk" ? "danger" : "tight";
  return (
    <>
      <PageHeader title="Today" action={<div className="today-actions"><IconBtn title="Refresh dashboard" disabled={busy} onClick={refreshFinancial}><DashboardIcon kind="refresh" /></IconBtn></div>} />
      {error && <p className="error">{error}</p>}
      {today && <>
        <div className="dashboard-overview"><Card className="today-hero">
          <div className="today-hero-row"><div><Pill tone={tone}>{allowance?.funding_shortfall ? "Savings plan needs attention" : rescue?.first_gap ? "Needs attention" : !optionalVerified ? "Plan needs verification" : allowance && optionalVerified && allowance.remaining > 0 ? "Allowance available" : statusLabel(today)}</Pill>{(!allowance || !optionalVerified || allowance.remaining <= 0) && <h2>{allowance ? allowance.remaining > 0 && optionalVerified ? "Your allowance" : allowance.funding_shortfall > 0 ? "Pause discretionary spending: your savings plan has a gap." : "Your allowance is paused. Review the plan below." : rescue?.first_gap ? rescue.gap_depends_on_living_holdback ? "Bills may squeeze everyday spending." : "A payment gap is coming. Let's make a plan." : !rescue?.normal_spending_entered && today.safety_status === "safe" ? "Your known bills look covered." : !optionalVerified ? "Check your plan before spending." : statusHeadline(today)}</h2>}</div><span className="dashboard-horizon"><DashboardIcon kind="calendar" /> 6-month plan</span></div>
          <div className="today-money-grid today-clarity-grid allowance-overview-grid">
            <Metric icon="wallet" label={allowance ? "Biweekly allowance" : "Free to use now"} value={optionalVerified ? exactMoney(breakdown?.free_to_use_now) : allowance?.remaining === 0 ? "$0.00 · paused" : "Not verified"} note={allowance ? `Through ${friendlyDate(allowance.period_end)}` : "Available now"} progress={allowance && optionalVerified && allowance.budget > 0 ? allowance.remaining / allowance.budget : undefined} prominent />
            <Metric icon="shield" label="Safety net" value={exactMoney(allowance?.emergency_current ?? breakdown?.cash_cushion_current)} note={allowance ? `${exactMoney(allowance.monthly_contribution)}/mo · ${exactMoney(allowance.emergency_goal)} goal` : "Current balance"} progress={allowance && allowance.emergency_current != null && allowance.emergency_goal > 0 ? allowance.emergency_current / allowance.emergency_goal : undefined} />
            <Metric icon="bill" label="Bills" value={exactMoney(breakdown?.required_planning)} note="6-month total" />
            <Metric icon="daily" label="Daily spending" value={exactMoney((breakdown?.normal_spending_planning ?? 0) - groceryTotal)} note="6-month total" />
            <Metric icon="groceries" label="Groceries" value={exactMoney(groceryTotal)} note="6-month total" />
            <Metric icon="sparkle" label="Discretionary plan" value={exactMoney(allowance?.total_discretionary_planned ?? breakdown?.free_to_use_now)} note="6 months · not spend now" />
          </div>
          <div className="dashboard-meta"><span>Recorded checking {exactMoney(today.cashflow.checking_balance)}</span><span>Updated {new Date(today.generated_at).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"})}</span></div>
          <div className="dashboard-drawers">
          {allowance && <details className="dated-payment-card"><summary><DashboardIcon kind="shield" /> Allowance details</summary><p className="today-money-explanation">Your two-week allowance is {exactMoney(allowance.budget)}; {exactMoney(allowance.spent)} is recorded as spent. Only the allowance remaining is available for discretionary spending now. {allowance.retention_percent != null && <>We release {100 - allowance.retention_percent}% of each safely planned discretionary allocation and leave {allowance.retention_percent}% ({exactMoney(allowance.retained_per_period)} per period) unspent to carry forward. This retained amount is included in the discretionary plan, separate from your emergency fund. </>} The six-month plan protects bills, daily needs, groceries, future allowances, and another {exactMoney(allowance.comfort_holdback)} of allowance breathing room. Emergency saving: {exactMoney(allowance.monthly_contribution)} per month toward {exactMoney(allowance.emergency_goal)}. Current safety net: {exactMoney(allowance.emergency_current)}; projected {exactMoney(allowance.projected_emergency)} by {friendlyDate(money?.planning_through)} if contributions happen as planned. Next allowance period starts {friendlyDate(allowance.next_reset)}. Recorded purchases consume this period's allowance; refreshing does not refill it.</p></details>}
          {allowance?.funding_shortfall ? <p className="error">Savings gap: {exactMoney(allowance.funding_shortfall)} · allowance paused</p> : null}
          {!allowance && optionalVerified && <details className="dated-payment-card"><summary><DashboardIcon kind="shield" /> Spending details</summary><p className="today-money-explanation">{statusSupport(today)} Free to use now is {exactMoney(breakdown?.free_to_use_now)} after six months of recorded bills and essentials. Update checking when it changes; Ledger does not check your bank automatically.</p></details>}
          {allowance && <details className="dated-payment-card"><summary><DashboardIcon kind="wallet" /> Record spending</summary><form className="planned-spending-form" onSubmit={event => void recordOptionalPurchase(event)}>
            <label>Record a discretionary purchase <input className="input" aria-label="Discretionary purchase amount" type="number" min="0.01" step="0.01" required value={optionalPurchase} onChange={event => setOptionalPurchase(event.target.value)} /></label>
            <Btn type="submit" disabled={busy}>Record allowance spending</Btn>
            <p className="field-hint">Use the amount you actually spent, even if it exceeded the allowance. Then update checking after the purchase clears.</p>
          </form></details>}
          {!optionalVerified && <details className="dated-payment-card"><summary>Review plan</summary><p className="today-money-explanation">{error ? "The latest calculation could not be verified. Refresh before relying on a spending amount." : today.checking_activity_pending ? "You marked a payment or income as done. Update your checking balance below before relying on an extra-spending amount." : rescue && !rescue.normal_spending_entered ? "Add a usual amount for groceries and everyday needs before treating any money as optional." : breakdown?.extra_reason || "Check the dated plan before optional spending."}</p></details>}
          {paymentPlan && <details className="dated-payment-card"><summary><DashboardIcon kind="calendar" /> Payment schedule</summary>
            <div className="section-head"><p className="subtitle">Income, bills, and your weekly grocery trips are dated below. Ordinary daily spending is included in each running balance without filling this list with daily entries.</p><Link to="/money?section=bills">Update payments</Link></div>
            <p className="field-hint">Start with {exactMoney(paymentPlan.opening_checking)}. Add {exactMoney(breakdown?.income_planning)} expected income, subtract {exactMoney(breakdown?.required_planning)} required payments and {exactMoney(breakdown?.normal_spending_planning)} normal spending; projected six-month ending checking is {exactMoney(paymentPlan.projected_ending_checking)}. The lowest dated point is {exactMoney(paymentPlan.lowest_checking)}. Income is shown before payments on the same day; confirm it has cleared before relying on it. This running balance shows bills and essentials before emergency earmarks and allowances. The allowance calculation also protects monthly emergency saving and future pay periods; it is the spending limit to follow.</p>
            <ol className="dated-payment-list">{paymentSteps.map((row, index) => <li key={`${row.date}-${row.category}-${index}`} className="dated-payment-row"><div><strong>{friendlyDate(row.date)} · {row.kind === "income" ? "Expect" : row.awaiting_checking_update ? "Recorded · pending checking update:" : row.spending_kind === "groceries" ? "Plan groceries" : "Pay"} {row.name}</strong>{row.awaiting_checking_update && <span className="field-hint"> {exactMoney(row.pending_checking_amount)} is already reserved in this forecast. Do not pay or record it again; update checking after the balance includes it.</span>}{row.spending_kind === "groceries" && row.suggested_day && !row.awaiting_checking_update && <span className="field-hint"> Suggested shopping day; change it in Money if another day works better.</span>}{row.grace_used && <span className="field-hint"> Mortgage grace needed: due {friendlyDate(row.original_due_date)}, plan payment by {friendlyDate(row.date)} before the recorded late-fee deadline {friendlyDate(row.late_fee_date)}. Confirm income has cleared.</span>}{row.deferred_once && <span className="field-hint"> Approved one-time move from {friendlyDate(row.original_due_date)}; the next regular payment is unchanged.</span>}{!row.deferred_once && !row.grace_used && row.original_due_date && row.original_due_date < row.date && <span className="field-hint"> Originally due {friendlyDate(row.original_due_date)}; check whether it is already paid.</span>}</div><div className="dated-payment-amount"><strong>{row.kind === "income" ? "+" : "−"}{exactMoney(row.amount)}</strong><span>Then {exactMoney(row.projected_balance_after)}</span></div></li>)}</ol>
            <p className="field-hint">This is a planning order, not an automatic payment. If a grocery trip, paycheck, bill, or checking balance differs, update Money and refresh Today.</p>
          </details>}
          </div>
        </Card>
        {today.checking_activity_pending ? <Card><h3>Update checking to see your chart</h3><Pill tone="tight">Balance needed</Pill></Card> : <MoneyCashflowChart compact refreshKey={today?.generated_at ? Date.parse(today.generated_at) : 0} reserveSnapshot={reserveSnapshot} />}
        </div>
        {rescue && (rescue.first_gap || !rescue.normal_spending_entered || !rescue.cushion_configured || rescue.cushion_gap > 0) && (
          <Card className="cash-rescue-card"><details><summary>{rescue.first_gap ? `Payment gap · ${currency(rescue.needed_before_income)}` : "Plan notes"}</summary>
            <div className="section-head"><h3>{rescue.first_gap ? "Ways to get through the gap" : "Make the plan more reliable"}</h3><Link to="/money?section=misc">Update spending</Link></div>
            {rescue.first_gap && <p><strong>{currency(rescue.needed_before_income)}</strong> is needed to cover recorded payments{rescue.normal_spending_held > 0 ? " and estimated everyday spending through that date" : ""} before {rescue.next_income ? `income on ${friendlyDate(rescue.next_income.date)}` : "more income is recorded"}. The first pressure point is {rescue.first_gap.payment || "your balance"} on {friendlyDate(rescue.first_gap.date)}.</p>}
            {rescue.scenarios.length > 0 && <><p className="field-hint">These are questions to ask, not approved changes. Do not skip necessities or essential utilities. Keep each original due date unless every payee confirms new terms, fees, and any credit or service impact.</p><ul className="rescue-options">{rescue.scenarios.map((option, index) => <li key={`${option.payment}-${option.original_date}-${index}`}><strong>Ask about {option.changes.map(change => change.payment).join(" and ")}:</strong> Could {option.changes.map(change => `${currency(change.amount)} due ${friendlyDate(change.original_date)}`).join(" and ")} be paid on {friendlyDate(option.proposed_date)}? {option.closes_recorded_gap ? "If approved, recorded payments would be covered in this six-month view." : `${currency(option.remaining_gap_before_income)} would still be needed before that income.`}</li>)}</ul></>}
            {rescue.first_gap && rescue.scenarios.length === 0 && <p>There is no single recorded payment-date change that improves this gap before the next recorded income. Contact the payee early about a payment plan and review income or other safe help.</p>}
            {!rescue.normal_spending_entered && <p>Groceries and other everyday spending have not been entered. The gap above could be larger once they are included. <Link to="/money?section=misc">Add usual spending</Link>.</p>}
            {!rescue.cushion_configured && <p>Your cash cushion goal is not set. <Link to="/money?section=cash_on_hand">Set a cushion target</Link> so the plan protects money for surprises.</p>}
            {rescue.cushion_configured && rescue.cushion_gap > 0 && <p>Your projected cash may dip below your cushion goal. Rebuilding it gradually is safer than promising all your optional money at once.</p>}
            {rescue.first_gap && <p className="field-hint">If a listed charge already cleared, mark that occurrence paid in Money and refresh Today. Ledger does not check your bank automatically.</p>}
          </details></Card>
        )}

        {today.wealth_plan && rescue?.normal_spending_entered && rescue?.cushion_configured && today.wealth_plan.free_cash > 0 && <Card className="wealth-plan-card"><details><summary>Long-term plan</summary><div className="section-head"><div><h3>What to do with longer-term surplus</h3><p className="subtitle">This comes from money already included in the free-to-use total above. Do not add it again.</p></div><Link to="/household">Adjust plan</Link></div><div className="today-money-grid"><Metric label="Save" value={currency(today.wealth_plan.allocation.save)} /><Metric label="Grow" value={currency(today.wealth_plan.allocation.grow)} /><Metric label="Keep free" value={currency(today.wealth_plan.allocation.free)} /></div><p className="field-hint">{today.wealth_plan.why}</p>{today.top_wealth_action && <p className="today-money-explanation"><strong>Suggested next step:</strong> {today.top_wealth_action.title}</p>}</details></Card>}
        {today.wealth_plan && rescue && (!rescue.normal_spending_entered || !rescue.cushion_configured) && <Card className="wealth-plan-card"><details><summary>Complete your plan</summary><h3>Save, grow, and free-to-spend plan</h3><p>Before relying on this split, add your everyday spending and a cash cushion goal. Ledger will recalculate it when you do.</p><Link to="/money?section=misc">Complete your spending plan</Link></details></Card>}

        <div className="today-columns">
          <Card><div className="section-head"><h3>Due soon</h3><Link to="/money?section=bills">Manage money</Link></div>
            <details className="dashboard-help"><summary>How to use</summary><p className="field-hint">Mark an item only when it happened. Pending purchases stay reserved until checking includes them. Update checking after your bank balance changes.</p></details>
            <DueSoonItems today={today} busy={busy} onMark={mark} onRecord={(item) => { void recordSpending(item); }} onCushion={async () => { setBusy(true); try { await api.recordCushionContribution(today.cushion_step.amount); await load(); } catch (err) { setError(err instanceof Error ? err.message : "Could not record the cushion step."); } finally { setBusy(false); } }} />

          </Card>
          <div className="dashboard-tools"><CheckingQuickUpdate pendingActivity={today.checking_activity_pending} onSaved={load} />
            <Card className="dashboard-tool"><details><summary><DashboardIcon kind="chat" /> Ask Ledger</summary><FinancialAssistant /></details></Card>
          </div>
        </div>
      </>}
    </>
  );
}

type DashboardIconKind = "wallet" | "shield" | "bill" | "daily" | "groceries" | "sparkle" | "refresh" | "calendar" | "chat";
const dashboardPaths: Record<DashboardIconKind, string> = {
  wallet: "M3 5h16v14H3z M3 5l13-3v3 M15 10h6v5h-6z M17 12.5h1",
  shield: "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6",
  bill: "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 7h6 M9 11h6 M9 15h4",
  daily: "M4 3v6c0 3 6 3 6 0V3 M7 3v18 M17 3c-4 4-4 9 0 9h2 M19 3v18",
  groceries: "M2 3h3l3 12h11l3-9H6 M9 20h1 M18 20h1",
  sparkle: "M12 2l3 7 7 3-7 3-3 7-3-7-7-3 7-3z",
  refresh: "M20 7V2 M20 7h-5 M20 7a9 9 0 1 0 1 8",
  calendar: "M4 5h16v16H4z M4 10h16 M8 2v6 M16 2v6 M8 14h2 M14 14h2",
  chat: "M3 3h18v14H9l-6 4z M7 8h10 M7 12h7",
};
function DashboardIcon({ kind }: { kind: DashboardIconKind }) {
  return <svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={dashboardPaths[kind]} /></svg>;
}
function Metric({ label, value, note, icon, progress, prominent }: { label: string; value: string; note?: string; icon?: DashboardIconKind; progress?: number; prominent?: boolean }) {
  return <div className={`today-metric${prominent ? " prominent" : ""}`}><span className="dashboard-metric-label">{icon && <DashboardIcon kind={icon} />}{label}</span><strong>{value}</strong>{note && <small>{note}</small>}{progress != null && <progress aria-label={`${label} progress`} max={1} value={Math.max(0, Math.min(1, progress))} />}</div>;
}

function localDate(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function DueSoonItems({ today, busy, onMark, onRecord, onCushion }: {
  today: V2TodayPayload;
  busy: boolean;
  onMark: (row: V2TodayPayload["immediate_obligations"][number]) => Promise<void>;
  onRecord: (row: PlannedSpendingAttention) => void;
  onCushion: () => Promise<void>;
}) {
  type Entry =
    | { kind: "scheduled"; date: string; row: V2TodayPayload["immediate_obligations"][number] }
    | { kind: "groceries" | "daily"; date: string; row: PlannedSpendingAttention }
    | { kind: "cushion"; date: string };
  const entries: Entry[] = [
    ...today.immediate_obligations.map((row): Entry => ({ kind: "scheduled", date: row.due_date || "", row })),
    ...today.spending_attention.grocery_trips
      .filter((row) => row.awaiting_checking_update || (!row.recorded && row.recorded_amount <= 0))
      .map((row): Entry => ({ kind: "groceries", date: row.planned_date, row })),
    ...today.spending_attention.everyday
      .filter((row) => row.awaiting_checking_update || (!row.recorded && row.recorded_amount <= 0))
      .map((row): Entry => ({ kind: "daily", date: row.planned_date, row })),
    ...(today.cushion_step.amount > 0 && !today.cushion_step.recorded
      ? [{ kind: "cushion" as const, date: today.cushion_step.date }]
      : []),
  ].sort((a, b) => a.date.localeCompare(b.date) || a.kind.localeCompare(b.kind));
  if (!entries.length) return <p className="subtitle">Nothing needs your attention soon.</p>;
  return <div className="due-soon-checklist">{entries.map((entry, index) => {
    if (entry.kind === "scheduled") {
      const row = entry.row;
      const canResolve = row.kind === "income" ? isReceivable(row) : isPayable(row);
      const verb = row.kind === "income" ? "Mark income received" : "Mark payment paid";
      return <DueSoonRow key={`scheduled-${row.kind}-${row.name}-${entry.date}-${index}`}
        title={row.name || "Scheduled item"}
        subtitle={`${row.kind === "income" ? "Expected" : "Due"} ${friendlyDate(entry.date)}`}
        trailing={`${row.kind === "income" ? "+" : "−"}${currency(row.amount)}`}
        tone={row.kind === "income" ? "safe" : "danger"}
        actionLabel={busy ? "Updating…" : canResolve ? verb : "Open Money to review this item"}
        disabled={busy || !canResolve}
        onResolve={() => void onMark(row)} />;
    }
    if (entry.kind === "cushion") {
      const canRecord = today.cushion_step.can_record ?? entry.date <= localDate();
      return <DueSoonRow key="cushion-step" title="Safety cushion"
        subtitle={`${friendlyDate(entry.date)} · ${!canRecord && entry.date <= localDate() ? "Review funding" : "Monthly deposit"}`}
        trailing={exactMoney(today.cushion_step.amount)} tone="default"
        actionLabel={busy ? "Updating…" : canRecord ? `Mark safety cushion deposited (${exactMoney(today.cushion_step.amount)})`
          : entry.date > localDate() ? `Available to record on ${friendlyDate(entry.date)}` : "Review cushion funding before recording"}
        disabled={busy || today.cushion_step.recorded || !canRecord} resolved={today.cushion_step.recorded}
        onResolve={() => void onCushion()} />;
    }
    const row = entry.row;
    const recorded = row.recorded || row.recorded_amount > 0;
    const isToday = entry.date === localDate();
    const canRecord = entry.kind === "groceries" ? entry.date <= localDate() : isToday;
    const title = entry.kind === "groceries" ? "Groceries" : row.name;
    const actionLabel = row.awaiting_checking_update ? `${title} recorded; awaiting checking update` : recorded ? `${title} already recorded as spent`
      : busy ? "Updating…"
      : canRecord ? `Mark ${title} paid (${exactMoney(row.planned_amount)})`
      : `Available to record on ${friendlyDate(entry.date)}`;
    return <DueSoonRow key={`${entry.kind}-${row.category_id}-${entry.date}`}
      title={title}
      subtitle={`${friendlyDate(entry.date)} · ${recorded ? `${exactMoney(row.recorded_amount)} recorded` : "Planned"}`}
      trailing={`−${exactMoney(recorded ? row.recorded_amount : row.planned_amount)}`}
      tone="danger" actionLabel={actionLabel} disabled={busy || recorded || !canRecord} resolved={recorded && !row.awaiting_checking_update} pending={row.awaiting_checking_update}
      onResolve={() => onRecord(row)} />;
  })}</div>;
}

function DueSoonRow({
  title, subtitle, trailing, tone, actionLabel, disabled = false, resolved = false, pending = false, onResolve,
}: {
  title: string;
  subtitle: string;
  trailing: string;
  tone: "default" | "danger" | "safe";
  actionLabel: string;
  disabled?: boolean;
  resolved?: boolean;
  pending?: boolean;
  onResolve: () => void;
}) {
  return <div className="list-row due-soon-row">
    <div className="list-row-body"><div className="list-row-title">{title}</div><div className="list-row-sub">{subtitle}</div></div>
    <div className={`list-row-trail${tone === "danger" ? " danger" : tone === "safe" ? " safe" : ""}`}>{trailing}</div>
    {pending ? <Pill tone="tight">Pending</Pill> : <button type="button" className={`due-soon-resolve${resolved ? " is-resolved" : ""}`} onClick={onResolve}
      aria-label={actionLabel} title={actionLabel} disabled={disabled}><IconCheckCircle /></button>}
  </div>;
}

function CheckingQuickUpdate({ pendingActivity, onSaved }: {
  pendingActivity: number;
  onSaved: () => Promise<void>;
}) {
  return <Card className="dashboard-tool"><details><summary><DashboardIcon kind="wallet" /> Update checking {pendingActivity > 0 && <Pill tone="tight">{pendingActivity} pending</Pill>}</summary>
    <div className="section-head"><h3>Balances</h3><Link to="/money?section=cash_on_hand">Manage</Link></div>
    <CashProfileForm onSaved={() => { void onSaved(); }} />
  </details></Card>;
}

function statusLabel(today: V2TodayPayload): string { return today.safety_status === "safe" ? "Money available" : today.safety_status === "at_risk" ? "Needs attention" : "Money looks tight"; }
function statusHeadline(today: V2TodayPayload): string {
  if (today.cashflow.checking_balance != null && today.cashflow.checking_balance <= 0) return today.cashflow.checking_balance < 0 ? "Your checking balance is negative." : "You have no money in checking.";
  if (today.financial_state.today_breakdown?.free_to_use_now === 0) return "There is no money free to spend right now.";
  return today.safety_status === "safe" ? "You have money left after your plan." : today.safety_status === "at_risk" ? "Your plan needs attention." : "Money looks tight ahead.";
}
function statusSupport(today: V2TodayPayload): string {
  const coverage = today.financial_state.today_breakdown?.coverage_message;
  if (coverage) return coverage;
  const warning = todayWarning(today.cashflow.warnings?.[0]);
  // A safe household can still have a data-quality note, but an unclassified
  // warning must not contradict the reassuring headline. Keep only concrete
  // missing-date guidance in the safe state; other warnings belong in details.
  if (warning && (today.safety_status !== "safe" || warning.includes("next payday") || warning.includes("next income"))) return warning;
  return today.safety_status === "safe" ? "Your known bills and normal spending are covered for the period ahead." : "Review the items below before spending extra money.";
}
function friendlyDate(value?: string | null): string { if (!value) return "an unknown date"; const date = new Date(`${value.slice(0, 10)}T12:00:00`); return Number.isNaN(date.getTime()) ? value.slice(0, 10) : date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }); }
