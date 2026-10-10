import { FormEvent, useCallback, useEffect, useState } from "react";
import { BtnSubmit, Field, Input, Segmented, Select, Toggle } from "../ui";
import { api } from "../../api";

export type HousingMode = "rent" | "mortgage";

type MortgageSummary = {
  id: string;
  lender: string;
  current_balance: number;
  interest_rate: number;
  monthly_payment: number;
  escrow_amount?: number;
  start_date?: string | null;
  due_by_date?: string | null;
  late_by_date?: string | null;
  property_value_estimate?: number | null;
  recurrence?: string;
};

type BorrowerHousing = {
  housing_status?: string;
  monthly_rent?: number;
  rent_due_date?: string | null;
};

export function HousingForm({
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
  const [mode, setMode] = useState<HousingMode>("rent");
  const [borrower, setBorrower] = useState<BorrowerHousing | null>(null);
  const [mortgage, setMortgage] = useState<MortgageSummary | null>(null);
  const [mortgageRepeats, setMortgageRepeats] = useState(true);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    const [b, mortgages] = await Promise.all([
      api.borrower().catch(() => null),
      api.mortgages().catch(() => []),
    ]);
    const profile = (b as BorrowerHousing) || {};
    const list = mortgages as { account: Record<string, unknown> }[];
    const first = list[0]?.account;
    const status = (profile.housing_status || "rent").toLowerCase() as HousingMode;
    setMode(status === "mortgage" ? "mortgage" : "rent");
    setBorrower(profile);
    if (first) {
      setMortgage({
        id: String(first.id),
        lender: String(first.lender || ""),
        current_balance: Number(first.current_balance || 0),
        interest_rate: Number(first.interest_rate || 0),
        monthly_payment: Number(first.monthly_payment || 0),
        escrow_amount: Number(first.escrow_amount || 0),
        start_date: first.start_date ? String(first.start_date).slice(0, 10) : null,
        due_by_date: first.due_by_date
          ? String(first.due_by_date).slice(0, 10)
          : first.start_date
            ? String(first.start_date).slice(0, 10)
            : null,
        late_by_date: first.late_by_date ? String(first.late_by_date).slice(0, 10) : null,
        property_value_estimate: first.property_value_estimate
          ? Number(first.property_value_estimate)
          : null,
        recurrence: String(first.recurrence || "monthly"),
      });
      setMortgageRepeats(
        (first.recurrence || "monthly") !== "once" && (first.recurrence || "monthly") !== "none"
      );
    } else {
      setMortgage(null);
      setMortgageRepeats(true);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const saveRent = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const fd = new FormData(e.currentTarget);
    const body = {
      housing_status: "rent",
      monthly_rent: parseFloat(String(fd.get("monthly_rent"))),
      rent_due_date: String(fd.get("rent_due_date")),
    };
    try {
      const vr = await api.validateEntry("rent", body);
      if (!vr.ok) {
        setErr(vr.errors.join(" "));
        onSaveFailed?.();
        return;
      }
      await api.upsertBorrower(body);
      onSaved();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Save failed");
      onSaveFailed?.();
    } finally {
      setBusy(false);
    }
  };

  const saveMortgage = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const fd = new FormData(e.currentTarget);
    const balance = parseFloat(String(fd.get("balance")));
    const payload = {
      lender: String(fd.get("lender")),
      current_balance: balance,
      original_balance: balance,
      interest_rate: parseFloat(String(fd.get("rate"))),
      monthly_payment: parseFloat(String(fd.get("payment"))),
      escrow_amount: parseFloat(String(fd.get("escrow")) || "0"),
      due_by_date: String(fd.get("due_by_date")) || null,
      late_by_date: String(fd.get("late_by_date")) || null,
      start_date: String(fd.get("due_by_date")) || null,
      property_value_estimate: parseFloat(String(fd.get("value"))),
      recurrence: mortgageRepeats ? String(fd.get("recurrence") || "monthly") : "none",
    };
    try {
      const vr = await api.validateEntry("mortgage", payload);
      if (!vr.ok) {
        setErr(vr.errors.join(" "));
        onSaveFailed?.();
        return;
      }
      if (mortgage?.id) {
        await api.updateMortgage(mortgage.id, payload);
      } else {
        await api.createMortgage(payload);
      }
      await load();
      onSaved();
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Save failed");
      onSaveFailed?.();
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return <p className="field-hint">Loading housing…</p>;
  }

  return (
    <div className="housing-form">
      <Segmented
        options={[
          { value: "rent", label: "Rent" },
          { value: "mortgage", label: "Mortgage" },
        ]}
        value={mode}
        onChange={setMode}
      />

      {mode === "rent" ? (
        <form
          id={formId}
          className="quick-add-form"
          data-housing-mode="rent"
          onSubmit={saveRent}
        >
          <Field label="Monthly rent">
            <Input
              name="monthly_rent"
              type="number"
              inputMode="decimal"
              required
              defaultValue={borrower?.monthly_rent ?? ""}
            />
          </Field>
          <Field label="Due date (each month)">
            <Input
              name="rent_due_date"
              type="date"
              required
              defaultValue={
                borrower?.rent_due_date ? String(borrower.rent_due_date).slice(0, 10) : ""
              }
            />
          </Field>
          {err && <p className="error">{err}</p>}
          {!wizardMode && (
            <BtnSubmit full disabled={busy}>
              Save rent
            </BtnSubmit>
          )}
        </form>
      ) : (
        <form
          id={formId}
          className="quick-add-form"
          data-housing-mode="mortgage"
          onSubmit={saveMortgage}
        >
          <Toggle
            label="Repeats in monthly forecast"
            checked={mortgageRepeats}
            onChange={setMortgageRepeats}
          />
          <Field label="Lender">
            <Input name="lender" required defaultValue={mortgage?.lender || ""} />
          </Field>
          <Field label="Current balance">
            <Input
              name="balance"
              type="number"
              inputMode="decimal"
              required
              defaultValue={mortgage?.current_balance ?? ""}
            />
          </Field>
          <Field label="Interest rate %">
            <Input
              name="rate"
              type="number"
              inputMode="decimal"
              required
              defaultValue={mortgage?.interest_rate ?? ""}
            />
          </Field>
          <Field label="Monthly P&amp;I">
            <Input
              name="payment"
              type="number"
              inputMode="decimal"
              required
              defaultValue={mortgage?.monthly_payment ?? ""}
            />
          </Field>
          <Field label="Escrow / month">
            <Input
              name="escrow"
              type="number"
              inputMode="decimal"
              defaultValue={mortgage?.escrow_amount ?? 0}
            />
          </Field>
          {mortgageRepeats && (
            <Field label="Frequency">
              <Select name="recurrence" defaultValue={mortgage?.recurrence || "monthly"}>
                <option value="monthly">Monthly</option>
              </Select>
            </Field>
          )}
          <Field
            label="Due by date"
            hint="Cashflow reserves money by this date each month."
          >
            <Input
              name="due_by_date"
              type="date"
              required={mortgageRepeats}
              defaultValue={mortgage?.due_by_date || mortgage?.start_date || ""}
            />
          </Field>
          <Field
            label="Late by date (grace)"
            hint="Optional. AI treats the payment as late only after this date."
          >
            <Input
              name="late_by_date"
              type="date"
              defaultValue={mortgage?.late_by_date || ""}
            />
          </Field>
          <Field label="Property value">
            <Input
              name="value"
              type="number"
              inputMode="decimal"
              required
              defaultValue={mortgage?.property_value_estimate ?? ""}
            />
          </Field>
          {err && <p className="error">{err}</p>}
          {!wizardMode && (
            <BtnSubmit full disabled={busy}>
              {mortgage?.id ? "Save mortgage" : "Add mortgage"}
            </BtnSubmit>
          )}
        </form>
      )}
    </div>
  );
}
