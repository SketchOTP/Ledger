import { FormEvent, useEffect, useState } from "react";
import { LiquidAccountEditor, type LiquidAccountRow } from "./RecordEditors";
import { Btn, BtnSubmit, Field, IconBtn, IconPlus, Input, ListRow, Select, Toggle } from "../ui";
import { api } from "../../api";

type Borrower = {
  checking_balance?: number;
  emergency_fund_balance?: number;
  credit_score?: number | null;
  target_buffer?: number;
  cushion_monthly_contribution?: number;
  pay_period_allowance_enabled?: boolean;
  cash_cushion_balance?: number | null;
};

export function CashProfileForm({
  onSaved,
  wizardMode,
  formId,
  onSaveFailed,
}: {
  onSaved: () => void;
  wizardMode?: boolean;
  formId?: string;
  onSaveFailed?: () => void;
}) {
  const [borrower, setBorrower] = useState<Borrower | null>(null);
  const [accounts, setAccounts] = useState<LiquidAccountRow[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [includeCashflow, setIncludeCashflow] = useState(true);
  const [includesRecordedSpending, setIncludesRecordedSpending] = useState(false);

  const reloadAccounts = () =>
    api.liquidAccounts().then((x) => setAccounts(x as LiquidAccountRow[]));

  useEffect(() => {
    Promise.all([api.borrower(), api.liquidAccounts()]).then(([b, a]) => {
      setBorrower((b as Borrower) || {});
      setAccounts((a as LiquidAccountRow[]) || []);
    });
  }, []);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const fd = new FormData(e.currentTarget);
    const body = {
      checking_balance: parseFloat(String(fd.get("checking"))) || 0,
      emergency_fund_balance: parseFloat(String(fd.get("savings"))) || 0,
      credit_score: fd.get("credit_score") ? parseInt(String(fd.get("credit_score")), 10) : null,
      pay_period_allowance_enabled: borrower?.pay_period_allowance_enabled ?? true,
      cushion_monthly_contribution: Number(fd.get("cushion_monthly_contribution")),
      target_buffer: parseFloat(String(fd.get("target_buffer"))) || 0,
      cash_cushion_balance: fd.get("cash_cushion_balance") === "" ? null : Number(fd.get("cash_cushion_balance")),
      // A current bank balance may still exclude a processing purchase.
      includes_recorded_spending: includesRecordedSpending,
      checking_balance_confirmed: true,
    };
    try {
      const vr = await api.validateEntry("cash_balance", body);
      if (!vr.ok) {
        setErr(vr.errors.join(" "));
        onSaveFailed?.();
        return;
      }
      await api.upsertBorrower(body);
      setBorrower(body);
      setIncludesRecordedSpending(false);
      onSaved();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Save failed");
      onSaveFailed?.();
    } finally {
      setBusy(false);
    }
  };

  if (!borrower) {
    return <p className="field-hint">Loading balances…</p>;
  }

  return (
    <form id={formId} className="cash-profile-form" onSubmit={submit}>
      <div className="cash-profile-grid">
        <Field label="Checking" hint="Enter your current bank balance. Processing purchases stay reserved until you confirm they are already deducted from this balance.">
          <Input
            name="checking"
            type="number"
            step="0.01"
            inputMode="decimal"
            defaultValue={borrower.checking_balance ?? 0}
          />
        </Field>
        <Field label="Savings / emergency fund">
          <Input
            name="savings"
            type="number"
            inputMode="decimal"
            defaultValue={borrower.emergency_fund_balance ?? 0}
          />
        </Field>
        <Field label="Credit score">
          <Input
            name="credit_score"
            type="number"
            inputMode="numeric"
            placeholder="e.g. 720"
            defaultValue={borrower.credit_score ?? ""}
          />
        </Field>
        <Field label="Emergency fund goal">
          <Input
            name="target_buffer"
            type="number"
            inputMode="decimal"
            placeholder="Money to keep for surprises"
            defaultValue={
              borrower.target_buffer && borrower.target_buffer > 0
                ? borrower.target_buffer
                : ""
            }
          />
        </Field>
        <Field label="Monthly emergency saving" hint="Protect this amount each calendar month until the emergency fund reaches its goal.">
          <Input name="cushion_monthly_contribution" type="number" min="0" step="0.01" defaultValue={borrower.cushion_monthly_contribution ?? 100} />
        </Field>
        <Field label="Safety net current balance" hint="This is money already included in checking or savings, not another account. Update it if you use some of your cushion.">
          <Input name="cash_cushion_balance" type="number" min="0" step="0.01" inputMode="decimal" placeholder="Enter your current cushion amount" defaultValue={borrower.cash_cushion_balance ?? ""} />
        </Field>
      </div>

      <label className="field-hint"><input type="checkbox" name="includes_recorded_spending" checked={includesRecordedSpending} onChange={event => setIncludesRecordedSpending(event.currentTarget.checked)} /> All recorded purchases are already deducted from this checking balance.</label>
      <p className="field-hint">Leave this unchecked while a purchase is still processing and has not reduced the balance above. Ledger cannot check your bank; saving alone keeps recorded purchases pending.</p>

      <div className="money-record-list">
        {accounts.map((a) =>
          editingId === a.id ? (
            <LiquidAccountEditor
              key={a.id}
              row={a}
              onCancel={() => setEditingId(null)}
              onDone={() => {
                setEditingId(null);
                reloadAccounts().then(() => onSaved());
              }}
            />
          ) : (
            <ListRow
              key={a.id}
              title={`${a.account_name} (${a.account_type})`}
              subtitle={`${a.institution || "Bank account"}${a.include_in_cashflow ? " · included in cashflow" : " · excluded from cashflow"}`}
              trailing={`$${Number(a.balance || 0).toLocaleString()}`}
              onEdit={() => {
                setAdding(false);
                setEditingId(a.id);
              }}
              onDelete={() => api.deleteLiquidAccount(a.id).then(reloadAccounts)}
            />
          )
        )}
      </div>

      {!adding ? (
        <IconBtn
          title="Add money you have"
          onClick={() => {
            setEditingId(null);
            setAdding(true);
          }}
          style={{ marginTop: "0.5rem" }}
        >
          <IconPlus />
        </IconBtn>
      ) : (
        <div className="card" style={{ marginTop: "0.75rem", padding: "0.75rem" }}>
          <h4 style={{ margin: "0 0 0.5rem" }}>Add money you have</h4>
          <p className="field-hint">Where is this money?</p>
          <div className="cash-profile-grid">
            <Field label="Name">
              <Input name="acc_name" placeholder="Joint checking" />
            </Field>
            <Field label="Type">
              <Select name="acc_type" defaultValue="checking">
                <option value="checking">Checking</option>
                <option value="savings">Savings</option>
              </Select>
            </Field>
            <Field label="Where is it held?">
              <Input name="acc_bank" placeholder="Bank name" />
            </Field>
            <Field label="Balance">
              <Input name="acc_balance" type="number" inputMode="decimal" defaultValue="0" />
            </Field>
            <Field label="Current APY" hint="Used only to compare your current yield with researched alternatives.">
              <Input name="acc_apy" type="number" min="0" max="100" step="0.01" inputMode="decimal" placeholder="Optional" />
            </Field>
            <Field label="APY as of">
              <Input name="acc_apy_as_of" type="date" />
            </Field>
          </div>
          <Toggle label="Include in cashflow" checked={includeCashflow} onChange={setIncludeCashflow} />
          <div className="inline-edit-actions">
            <Btn type="button" variant="secondary" onClick={() => setAdding(false)}>
              Cancel
            </Btn>
            <Btn
              type="button"
              onClick={async () => {
                const root = document.getElementById(formId || "") as HTMLFormElement | null;
                const scope = root ?? document.querySelector(".cash-profile-form");
                if (!scope) return;
                const fd = new FormData(scope as HTMLFormElement);
                await api.createLiquidAccount({
                  account_name: String(fd.get("acc_name") || "Account"),
                  account_type: String(fd.get("acc_type") || "checking"),
                  institution: String(fd.get("acc_bank") || ""),
                  balance: parseFloat(String(fd.get("acc_balance") || "0")) || 0,
                  current_apy: fd.get("acc_apy") === "" ? null : Number(fd.get("acc_apy")),
                  apy_as_of: String(fd.get("acc_apy_as_of") || "") || null,
                  include_in_cashflow: includeCashflow,
                });
                await reloadAccounts();
                setAdding(false);
                setIncludeCashflow(true);
                onSaved();
              }}
            >
              Add
            </Btn>
          </div>
        </div>
      )}
      {err && <p className="error">{err}</p>}
      {!wizardMode && (
        <BtnSubmit full disabled={busy}>
          Save profile &amp; balances
        </BtnSubmit>
      )}
    </form>
  );
}
