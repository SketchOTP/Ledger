import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Btn, Card, PageHeader, Segmented } from "../components/ui";
import { api, type CashflowPlan } from "../api";
import {
  CASHFLOW_PLAN_HORIZONS,
  type CashflowPlanHorizon,
} from "../lib/cashflowPlanHorizons";

export default function CashFlow() {
  const [cf, setCf] = useState<Record<string, unknown> | null>(null);
  const [plan, setPlan] = useState<CashflowPlan | null>(null);
  const [horizon, setHorizon] = useState<CashflowPlanHorizon>(180);
  const [loading, setLoading] = useState(true);

  const loadCf = useCallback(() => api.cashflow().then(setCf), []);

  useEffect(() => {
    void loadCf();
  }, [loadCf]);

  useEffect(() => {
    setLoading(true);
    api.cashflowPlan(horizon).then(setPlan).finally(() => setLoading(false));
  }, [horizon]);

  const refreshAll = () => {
    setLoading(true);
    void Promise.all([loadCf(), api.cashflowPlan(horizon).then(setPlan)]).finally(() =>
      setLoading(false)
    );
  };

  return (
    <>
      <PageHeader
        title="Cash flow"
        subtitle="What you have now, what's coming in, and what's due"
        action={
          <Btn variant="secondary" onClick={refreshAll} disabled={loading}>
            {loading ? "Updating…" : "Refresh"}
          </Btn>
        }
      />

      <div className="plain-box cashflow-flex-summary">
        <div className="cashflow-flex-row">
          <strong>Flexible today</strong>
          <p style={{ margin: "0.35rem 0 0" }}>
            <strong>
              ${Number(cf?.safe_to_spend_conservative ?? 0).toLocaleString()}
            </strong>
            <span style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
              {" "}
              — from checking only through this pay cycle; future deposits not counted as cash yet.
            </span>
          </p>
        </div>
        <div className="cashflow-flex-row" style={{ marginTop: "0.75rem" }}>
          <strong>Projected after 30 days</strong>
          <p style={{ margin: "0.35rem 0 0" }}>
            <strong>
              ${Number(
                (cf?.projected_cash_after_30_days ??
                  (cf?.thirty_day_outlook as { projected_balance?: number } | undefined)
                    ?.projected_balance ??
                  0) as number
              ).toLocaleString()}
            </strong>
            <span style={{ color: "var(--muted)", fontSize: "0.9rem" }}>
              {" "}
              — estimated checking if planned income lands and scheduled bills clear (before extra
              spending) · Status: <strong>{String(cf?.risk_level ?? "—")}</strong>
            </span>
          </p>
        </div>
        {(cf?.thirty_day_plan_summary ?? cf?.headline) ? (
          <p style={{ margin: "0.35rem 0 0", fontSize: "0.9rem" }}>
            <strong>30-day plan: </strong>
            {String(cf?.thirty_day_plan_summary ?? cf?.headline)}
          </p>
        ) : null}
        {cf?.monthly_obligations_total != null || cf?.monthly_bills_estimate != null ? (
          <p style={{ margin: "0.35rem 0 0", fontSize: "0.9rem", color: "var(--muted)" }}>
            Monthly forecast:{" "}
            <strong>
              $
              {Number(
                cf.monthly_obligations_total ??
                  Number(cf.monthly_bills_estimate ?? 0) +
                    Number(cf.monthly_debt_minimums ?? 0) +
                    Number(cf.monthly_credit_card_minimums ?? 0) +
                    Number(cf.monthly_mortgage_payments ?? 0)
              ).toLocaleString()}
              /mo
            </strong>{" "}
            (bills ${Number(cf.monthly_bills_estimate ?? 0).toLocaleString()} · debt $
            {Number(cf.monthly_debt_minimums ?? 0).toLocaleString()} · cards $
            {Number(cf.monthly_credit_card_minimums ?? 0).toLocaleString()} · mortgage $
            {Number(cf.monthly_mortgage_payments ?? 0).toLocaleString()}) — not duplicated in Bills.
          </p>
        ) : null}
        {cf?.next_pay_date != null && cf.next_pay_date !== "" ? (
          <p style={{ margin: "0.35rem 0 0", color: "var(--muted)", fontSize: "0.9rem" }}>
            Next paycheck expected: {String(cf.next_pay_date)}
          </p>
        ) : null}
      </div>

      <Card>
        <h3>Right now</h3>
        <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.95rem" }}>
          {[
            ["Checking balance", cf?.checking_balance ?? cf?.starting_cash],
            ["Due before next paycheck (bills + debt + cards + mortgage)", cf?.committed_until_pay],
            ["Safety buffer held back", cf?.minimum_buffer],
            ["Flexible today", cf?.safe_to_spend_conservative],
            [
              "Projected checking after 30 days",
              cf?.projected_cash_after_30_days ??
                (cf?.thirty_day_outlook as { projected_balance?: number } | undefined)
                  ?.projected_balance,
            ],
            [
              "Lowest 30-day cushion",
              cf?.lowest_30_day_cushion ?? cf?.safe_to_spend_timeline ?? cf?.safe_to_spend,
            ],
            ["Paycheck expected (not in “now”)", cf?.expected_income],
            ["Safe after paycheck (estimate)", cf?.safe_to_spend_after_paycheck],
            ["Projected balance end of period", cf?.projected_end_balance],
          ].map(([k, v]) => (
            <li key={String(k)}>
              {String(k)}: <strong>${Number(v as number).toFixed(0)}</strong>
            </li>
          ))}
        </ul>
        {Array.isArray(cf?.upcoming_payments) && cf.upcoming_payments.length > 0 ? (
          <>
            <h4 style={{ marginTop: "1rem", marginBottom: "0.35rem" }}>Due before payday</h4>
            <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.9rem" }}>
              {(cf.upcoming_payments as { date?: string; name?: string; amount?: number }[])
                .slice(0, 12)
                .map((e, i) => (
                  <li key={i}>
                    {e.date}: {e.name} — <strong>${Number(e.amount ?? 0).toFixed(0)}</strong>
                  </li>
                ))}
            </ul>
          </>
        ) : null}
      </Card>

      <Segmented
        value={String(horizon)}
        onChange={(v) => setHorizon(Number(v) as CashflowPlanHorizon)}
        options={CASHFLOW_PLAN_HORIZONS.map((option) => ({
          value: String(option.value),
          label: option.label,
        }))}
      />

      {plan && (
        <>
          <div className="plain-box">
            <strong>Your plan</strong>
            <ul style={{ margin: "0.5rem 0 0", paddingLeft: "1.1rem" }}>
              {plan.advice.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>

          <Card>
            <h3>Balance milestones</h3>
            {plan.milestones.map((m) => (
              <p key={m.day} style={{ margin: "0.35rem 0", fontSize: "0.9rem" }}>
                Day {m.day} ({m.date}): <strong>${m.projected_balance.toLocaleString()}</strong>
              </p>
            ))}
            <p style={{ fontSize: "0.85rem", color: "var(--muted)" }}>
              Lowest point: ${plan.lowest_balance.balance.toLocaleString()} on {plan.lowest_balance.date}
            </p>
          </Card>

          <Card>
            <h3>Upcoming paychecks, bills &amp; debt</h3>
            <ul style={{ margin: 0, paddingLeft: 0, listStyle: "none" }}>
              {plan.upcoming_events.slice(0, 15).map((e, i) => (
                <li
                  key={i}
                  style={{
                    padding: "0.5rem 0",
                    borderBottom: "1px solid var(--border)",
                    fontSize: "0.9rem",
                  }}
                >
                  {e.date} ·{" "}
                  {e.type === "paycheck" ? "💵" : e.type === "debt" ? "💳" : "📅"} {e.name}{" "}
                  <strong>${e.amount.toLocaleString()}</strong>
                </li>
              ))}
            </ul>
          </Card>
        </>
      )}

      <p style={{ textAlign: "center", fontSize: "0.85rem" }}>
        <Link to="/money?section=income">Update paycheck</Link> · <Link to="/money?section=bills">Edit bills</Link> ·{" "}
        <Link to="/money?section=debts">Edit debts</Link>
      </p>
    </>
  );
}
