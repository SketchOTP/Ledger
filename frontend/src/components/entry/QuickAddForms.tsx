import { FormEvent, useState } from "react";
import { Btn, BtnSubmit, Field, Input, Select, Toggle } from "../ui";
import { api } from "../../api";
import { BillTimingFields, billFormPaymentPayload, type BillMode, type BillPayment } from "../money/BillTimingFields";

type Kind =
  | "cash_balance"
  | "paycheck"
  | "bill"
  | "debt"
  | "credit_card"
  | "mortgage";

export function QuickAddForm({
  kind,
  onSaved,
  onCancel,
  wizardMode,
  formId,
  onSaveFailed,
}: {
  kind: Kind;
  onSaved: () => void;
  onCancel?: () => void;
  /** Hide Save button; parent submits via Next (setup wizard). */
  wizardMode?: boolean;
  formId?: string;
  onSaveFailed?: () => void;
}) {
  const [errors, setErrors] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [billMode, setBillMode] = useState<BillMode>("recurring");
  const [billPayments, setBillPayments] = useState<BillPayment[]>([{ due_date: "", amount: "" }]);
  const [debtRepeats, setDebtRepeats] = useState(true);
  const [cardRepeats, setCardRepeats] = useState(true);
  const [mortgageRepeats, setMortgageRepeats] = useState(true);
  const [busy, setBusy] = useState(false);

  const validate = async (data: Record<string, unknown>) => {
    const vr = await api.validateEntry(kind, data);
    setErrors(vr.errors);
    setWarnings(vr.warnings);
    return vr.ok;
  };

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErrors([]);
    const fd = new FormData(e.currentTarget);
    try {
      const raw = Object.fromEntries(fd.entries());
      const data = normalizeValidatePayload(kind, raw, {
        billMode,
        billPayments,
        debtRepeats,
        cardRepeats,
        mortgageRepeats,
      });
      if (!(await validate(data))) {
        onSaveFailed?.();
        setBusy(false);
        return;
      }
      if (kind === "cash_balance") {
        await api.upsertBorrower({
          checking_balance: parseFloat(String(raw.checking)) || 0,
          emergency_fund_balance: parseFloat(String(raw.savings)) || 0,
        });
      } else if (kind === "paycheck") {
        await api.createIncome({
          source_name: String(raw.name),
          amount: parseFloat(String(raw.amount)),
          pay_frequency: String(raw.frequency),
          next_pay_date: String(raw.next_pay) || null,
        });
      } else if (kind === "bill") {
        await api.createBill({
          name: String(raw.name),
          ...billFormPaymentPayload(billMode, raw, billPayments),
          required: true,
        });
      } else if (kind === "debt") {
        await api.createDebt({
          creditor: String(raw.creditor),
          balance: parseFloat(String(raw.balance)),
          apr: parseFloat(String(raw.apr)),
          minimum_payment: parseFloat(String(raw.min)),
          due_date: String(raw.due) || null,
          recurrence: debtRepeats ? String(raw.recurrence || "monthly") : "none",
          debt_type: String(raw.debt_type || "credit_card"),
        });
      } else if (kind === "credit_card") {
        await api.createCreditCard({
          issuer: String(raw.issuer),
          card_name: String(raw.card_name || raw.issuer),
          balance: parseFloat(String(raw.balance)),
          credit_limit: parseFloat(String(raw.limit)),
          apr: parseFloat(String(raw.apr)),
          minimum_payment: parseFloat(String(raw.min)),
          due_date: String(raw.due) || null,
          recurrence: cardRepeats ? String(raw.recurrence || "monthly") : "none",
        });
      } else if (kind === "mortgage") {
        await api.createMortgage({
          lender: String(raw.lender),
          current_balance: parseFloat(String(raw.balance)),
          original_balance: parseFloat(String(raw.balance)),
          interest_rate: parseFloat(String(raw.rate)),
          monthly_payment: parseFloat(String(raw.payment)),
          escrow_amount: parseFloat(String(raw.escrow) || "0"),
          due_by_date: String(raw.due_by_date) || null,
          late_by_date: String(raw.late_by_date) || null,
          start_date: String(raw.due_by_date) || null,
          property_value_estimate: parseFloat(String(raw.value)),
          recurrence: mortgageRepeats ? String(raw.recurrence || "monthly") : "none",
        });
      }
      (e.target as HTMLFormElement).reset();
      onSaved();
    } catch (err) {
      setErrors([err instanceof Error ? err.message : "Save failed"]);
      onSaveFailed?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      id={formId}
      className={`quick-add-form${wizardMode ? " wizard-inline-form-fields" : ""}`}
      onSubmit={submit}
    >
      {kind === "cash_balance" && (
        <>
          <Field label="Checking">
            <Input name="checking" type="number" inputMode="decimal" placeholder="0" />
          </Field>
          <Field label="Savings">
            <Input name="savings" type="number" inputMode="decimal" placeholder="0" />
          </Field>
        </>
      )}
      {kind === "paycheck" && (
        <>
          <Field label="Source">
            <Input name="name" placeholder="Main job" required />
          </Field>
          <Field label="Amount">
            <Input name="amount" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Frequency">
            <Select name="frequency" defaultValue="biweekly">
              <option value="weekly">Weekly</option>
              <option value="biweekly">Biweekly</option>
              <option value="monthly">Monthly</option>
            </Select>
          </Field>
          <Field label="Next pay date">
            <Input name="next_pay" type="date" required />
          </Field>
        </>
      )}
      {kind === "bill" && (
        <>
          <Field label="Name">
            <Input name="name" placeholder="For example, Ticketmaster via PayPal" required />
          </Field>
          <p className="field-hint">Record a purchase once, even if a payment provider processes it.</p>
          <BillTimingFields mode={billMode} onModeChange={setBillMode} payments={billPayments} onPaymentsChange={setBillPayments} />
        </>
      )}
      {kind === "debt" && (
        <>
          <Toggle label="Repeats in monthly forecast" checked={debtRepeats} onChange={setDebtRepeats} />
          <Field label="Creditor">
            <Input name="creditor" required />
          </Field>
          <Field label="Balance">
            <Input name="balance" type="number" inputMode="decimal" required />
          </Field>
          <Field label="APR %">
            <Input name="apr" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Minimum payment">
            <Input name="min" type="number" inputMode="decimal" required />
          </Field>
          {debtRepeats && (
            <Field label="Frequency">
              <Select name="recurrence" defaultValue="monthly">
                <option value="monthly">Monthly</option>
                <option value="weekly">Weekly</option>
                <option value="biweekly">Biweekly</option>
              </Select>
            </Field>
          )}
          <Field label="Payment due date">
            <Input name="due" type="date" required={debtRepeats} />
          </Field>
          <Field label="Type">
            <Select name="debt_type" defaultValue="loan">
              <option value="credit_card">Credit card</option>
              <option value="loan">Loan</option>
              <option value="medical">Medical</option>
              <option value="other">Other</option>
            </Select>
          </Field>
        </>
      )}
      {kind === "credit_card" && (
        <>
          <Toggle label="Repeats until paid off" checked={cardRepeats} onChange={setCardRepeats} />
          <Field label="Issuer">
            <Input name="issuer" required />
          </Field>
          <Field label="Card name (optional)">
            <Input name="card_name" placeholder="Same as issuer if blank" />
          </Field>
          <Field label="Balance">
            <Input name="balance" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Credit limit">
            <Input name="limit" type="number" inputMode="decimal" required />
          </Field>
          <Field label="APR %">
            <Input name="apr" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Minimum payment">
            <Input name="min" type="number" inputMode="decimal" required />
          </Field>
          {cardRepeats && (
            <Field label="Frequency">
              <Select name="recurrence" defaultValue="monthly">
                <option value="monthly">Monthly</option>
                <option value="weekly">Weekly</option>
                <option value="biweekly">Biweekly</option>
              </Select>
            </Field>
          )}
          <Field label="Payment due date">
            <Input name="due" type="date" required={cardRepeats} />
          </Field>
        </>
      )}
      {kind === "mortgage" && (
        <>
          <Toggle label="Repeats in monthly forecast" checked={mortgageRepeats} onChange={setMortgageRepeats} />
          <Field label="Lender">
            <Input name="lender" required />
          </Field>
          <Field label="Balance">
            <Input name="balance" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Rate %">
            <Input name="rate" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Monthly P&amp;I">
            <Input name="payment" type="number" inputMode="decimal" required />
          </Field>
          <Field label="Escrow / month">
            <Input name="escrow" type="number" inputMode="decimal" placeholder="0" />
          </Field>
          {mortgageRepeats && (
            <Field label="Frequency">
              <Select name="recurrence" defaultValue="monthly">
                <option value="monthly">Monthly</option>
              </Select>
            </Field>
          )}
          <Field label="Due by date">
            <Input name="due_by_date" type="date" required={mortgageRepeats} />
          </Field>
          <Field label="Late by date (grace)">
            <Input name="late_by_date" type="date" />
          </Field>
          <Field label="Property value">
            <Input name="value" type="number" inputMode="decimal" required />
          </Field>
        </>
      )}
      {errors.length > 0 && (
        <ul className="validation-errors">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="validation-warnings">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}

      {!wizardMode && (
        <div className="inline-edit-actions">
          {onCancel && (
            <Btn type="button" variant="secondary" full onClick={onCancel}>
              Cancel
            </Btn>
          )}
          <BtnSubmit full disabled={busy}>
            Save
          </BtnSubmit>
        </div>
      )}
    </form>
  );
}

function normalizeValidatePayload(
  kind: Kind,
  raw: Record<string, FormDataEntryValue>,
  opts?: {
    billMode?: BillMode;
    billPayments?: BillPayment[];
    debtRepeats?: boolean;
    cardRepeats?: boolean;
    mortgageRepeats?: boolean;
  }
): Record<string, unknown> {
  const s = (k: string) => String(raw[k] ?? "");
  if (kind === "cash_balance") {
    return {
      checking_balance: raw.checking,
      emergency_fund_balance: raw.savings,
    };
  }
  if (kind === "paycheck") {
    return {
      source_name: s("name"),
      amount: raw.amount,
      pay_frequency: s("frequency"),
      next_pay_date: s("next_pay"),
    };
  }
  if (kind === "bill") {
    return {
      name: s("name"),
      ...billFormPaymentPayload(opts?.billMode || "recurring", raw, opts?.billPayments || []),
    };
  }
  if (kind === "debt") {
    const repeats = opts?.debtRepeats ?? true;
    return {
      creditor: s("creditor"),
      balance: raw.balance,
      apr: raw.apr,
      minimum_payment: raw.min,
      due_date: s("due"),
      recurrence: repeats ? s("recurrence") || "monthly" : "none",
      debt_type: s("debt_type") || "credit_card",
    };
  }
  if (kind === "credit_card") {
    return {
      issuer: s("issuer"),
      balance: raw.balance,
      credit_limit: raw.limit,
      apr: raw.apr,
      minimum_payment: raw.min,
      due_date: s("due"),
    };
  }
  if (kind === "mortgage") {
    const repeats = opts?.mortgageRepeats ?? true;
    return {
      lender: s("lender"),
      current_balance: raw.balance,
      interest_rate: raw.rate,
      monthly_payment: raw.payment,
      escrow_amount: raw.escrow,
      due_by_date: s("due_by_date"),
      late_by_date: s("late_by_date"),
      start_date: s("due_by_date"),
      property_value_estimate: raw.value,
      recurrence: repeats ? s("recurrence") || "monthly" : "none",
    };
  }
  return { name: s("name"), preferred_brand: s("brand") };
}
