import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, PageHeader, Segmented } from "../components/ui";
import { api, type Forecast } from "../api";

const HORIZONS = [30, 60, 90, 180] as const;

function statusClass(status: string): string {
  if (status === "safe") return "risk-safe";
  if (status === "watch") return "";
  return "risk-danger";
}

function plainSummary(data: Forecast): string {
  if (data.first_shortfall_date) {
    return `You may run out of money on ${data.first_shortfall_date}. Plan to cut spending before then.`;
  }
  if (data.status === "safe") {
    return `Over the next ${data.horizon_days} days, cash should stay above your comfort zone if nothing big changes.`;
  }
  return `Cash may get tight — lowest balance about $${data.lowest_cash_day.cash_balance.toLocaleString()} around ${data.lowest_cash_day.date}.`;
}

export default function Forecast() {
  const [horizon, setHorizon] = useState<number>(90);
  const [data, setData] = useState<Forecast | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (days: number) => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.forecast(days));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Forecast failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(horizon);
  }, [horizon, load]);

  return (
    <>
      <PageHeader
        title="90-day outlook"
        subtitle="Will you have enough cash on each day? Plain language, no jargon."
      />

      <Segmented
        value={String(horizon)}
        onChange={(v) => setHorizon(Number(v))}
        options={HORIZONS.map((d) => ({ value: String(d), label: `${d}d` }))}
      />

      {loading && <p className="subtitle">Calculating…</p>}
      {error && <p className="error">{error}</p>}

      {data && (
        <>
          <div className={`plain-box ${statusClass(data.status)}`}>
            <strong>{data.status === "safe" ? "You're OK" : data.status === "watch" ? "Keep watch" : "Heads up"}</strong>
            <p style={{ margin: "0.5rem 0 0" }}>{plainSummary(data)}</p>
          </div>

          <Card>
            <h3>Key dates</h3>
            <p>
              Starting cash: <strong>${data.starting_cash.toLocaleString()}</strong>
              <br />
              Ending cash: <strong>${data.ending_cash.toLocaleString()}</strong>
              <br />
              Lowest day: <strong>{data.lowest_cash_day.date}</strong> at $
              {data.lowest_cash_day.cash_balance.toLocaleString()}
            </p>
            {data.first_shortfall_date && (
              <p className="risk-danger">First shortfall: {data.first_shortfall_date}</p>
            )}
          </Card>

          {data.recommendations.length > 0 && (
            <Card>
              <h3>What to do</h3>
              <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                {data.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      <p style={{ textAlign: "center", fontSize: "0.85rem" }}>
        <Link to="/money">Update balances &amp; bills</Link> for a better forecast
      </p>
    </>
  );
}
