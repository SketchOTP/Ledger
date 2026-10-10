import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type TransactionInput, type TransactionRow } from "../api";
import { Btn, Card, EmptyState, HeroStat, PageHeader } from "../components/ui";

const DEFAULT_CATEGORIES = [
  "groceries",
  "dining",
  "transport",
  "utilities",
  "shopping",
  "health",
  "entertainment",
  "other",
];

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function Transactions() {
  const [rows, setRows] = useState<TransactionRow[]>([]);
  const [cashflow, setCashflow] = useState<Record<string, any> | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [csvText, setCsvText] = useState("");
  const [form, setForm] = useState<TransactionInput>({
    amount: 0,
    category: "groceries",
    description: "",
    transaction_date: todayIso(),
  });

  const load = useCallback(() => {
    setLoading(true);
    setErr("");
    Promise.all([api.listTransactions(), api.cashflow()])
      .then(([txs, currentCashflow]) => {
        setRows(txs);
        setCashflow(currentCashflow);
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "Could not load spending log"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const safeToSpend = cashflow?.safe_to_spend_conservative ?? cashflow?.safe_to_spend;

  const recentSpend = useMemo(
    () => rows.filter((r) => r.transaction_date === todayIso()).reduce((s, r) => s + Math.max(0, r.amount), 0),
    [rows],
  );

  const afterTodaySpend =
    safeToSpend != null ? Math.max(0, Number(safeToSpend) - recentSpend) : null;

  const addTransaction = async () => {
    if (!form.amount || form.amount <= 0) {
      setErr("Enter a positive amount.");
      return;
    }
    setSaving(true);
    setErr("");
    setMsg("");
    try {
      await api.createTransaction(form);
      setForm((f) => ({ ...f, amount: 0, description: "" }));
      setMsg("Spending logged.");
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  const importCsv = async () => {
    if (!csvText.trim()) {
      setErr("Paste CSV content first.");
      return;
    }
    setSaving(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.importTransactionsCsv(csvText);
      setMsg(`Imported ${res.imported} transaction(s).`);
      if (res.errors?.length) {
        setErr(res.errors.slice(0, 3).join(" · "));
      }
      setCsvText("");
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Import failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="transactions-page">
      <PageHeader
        title="Spending log"
        subtitle="Manual entries stay on your device. See how today's spending relates to safe-to-spend."
      />

      {err && <p className="error">{err}</p>}
      {msg && <p style={{ color: "var(--safe)" }}>{msg}</p>}

      <div className="grid">
        <HeroStat
          label="Safe to spend"
          value={safeToSpend != null ? `$${Math.round(Number(safeToSpend)).toLocaleString()}` : "—"}
        />
        <HeroStat
          label="Logged today"
          value={`$${Math.round(recentSpend).toLocaleString()}`}
          tone={recentSpend > 0 ? "tight" : "default"}
        />
        <HeroStat
          label="After today's log"
          value={afterTodaySpend != null ? `$${Math.round(afterTodaySpend).toLocaleString()}` : "—"}
          tone={afterTodaySpend != null && afterTodaySpend <= 0 ? "danger" : "default"}
        />
      </div>

      <Card>
        <h3 style={{ marginTop: 0 }}>Add spending</h3>
        <div className="form-row">
          <label>
            Amount
            <input
              className="input"
              type="number"
              min={0}
              step={0.01}
              value={form.amount || ""}
              onChange={(e) => setForm((f) => ({ ...f, amount: Number(e.target.value) }))}
            />
          </label>
          <label>
            Category
            <select
              className="input"
              value={form.category}
              onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
            >
              {DEFAULT_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c.charAt(0).toUpperCase() + c.slice(1)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Date
            <input
              className="input"
              type="date"
              value={form.transaction_date}
              onChange={(e) => setForm((f) => ({ ...f, transaction_date: e.target.value }))}
            />
          </label>
        </div>
        <label style={{ display: "block", marginBottom: "0.75rem" }}>
          Note (optional)
          <input
            className="input"
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="Coffee, gas, etc."
          />
        </label>
        {safeToSpend != null && form.amount > 0 && (
          <p className="field-hint">
            If you spend ${form.amount.toLocaleString()}, safe-to-spend would be about $
            {Math.max(0, Math.round(Number(safeToSpend) - form.amount)).toLocaleString()} (informational —
            does not change your checking balance).
          </p>
        )}
        <div className="transactions-form-actions">
          <Btn onClick={addTransaction} disabled={saving}>
            {saving ? "Saving…" : "Log spending"}
          </Btn>
        </div>
      </Card>

      <Card>
        <h3 style={{ marginTop: 0 }}>Import CSV</h3>
        <p className="field-hint">
          Columns: <code>amount,category,description,transaction_date</code> (date as YYYY-MM-DD).
        </p>
        <textarea
          className="input"
          rows={5}
          value={csvText}
          onChange={(e) => setCsvText(e.target.value)}
          placeholder={"amount,category,description,transaction_date\n12.50,groceries,Store,2026-06-23"}
        />
        <Btn variant="secondary" onClick={importCsv} disabled={saving}>
          Import CSV
        </Btn>
      </Card>

      <Card>
        <h3 style={{ marginTop: 0 }}>Recent entries</h3>
        {loading && !rows.length ? (
          <p className="subtitle">Loading…</p>
        ) : rows.length === 0 ? (
          <EmptyState message="No spending logged yet — add a manual entry or import a CSV." />
        ) : (
          <ul className="money-record-list">
            {rows.slice(0, 50).map((row) => (
              <li key={row.id} className="list-row-simple">
                <span>
                  <strong>{row.description || row.category}</strong>
                  <span className="field-hint">
                    {" "}
                    · {row.category} · {String(row.transaction_date).slice(0, 10)}
                  </span>
                </span>
                <span>${Number(row.amount).toLocaleString()}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="field-hint" style={{ marginBottom: 0 }}>
          <Link to="/">Back to Home</Link> for safe-to-spend and today's plan.
        </p>
      </Card>
    </div>
  );
}
