import { FormEvent, useState } from "react";
import { Btn, BtnSubmit, Field, Input, Select, Toggle } from "../ui";
import { api } from "../../api";
import { BillTimingFields, billFormPaymentPayload, type BillMode, type BillPayment } from "./BillTimingFields";

type IncomeRow = {
  id: string;
  source_name: string;
  amount: number;
  pay_frequency: string;
  next_pay_date?: string | null;
};

export type LiquidAccountRow = {
  id: string;
  account_name: string;
  account_type: string;
  institution?: string;
  balance: number;
  current_apy?: number | null;
  apy_as_of?: string | null;
  include_in_cashflow: boolean;
};

type BillRow = {
  id: string;
  name: string;
  amount: number;
  due_date?: string | null;
  recurrence: string;
  payments?: { due_date: string; amount: number; paid?: boolean }[];
};

type DebtRow = {
  id: string;
  creditor: string;
  balance: number;
  apr: number;
  minimum_payment: number;
  due_date?: string | null;
  debt_type: string;
  recurrence?: string;
};

type MortgageRow = {
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

function recurrenceLabel(r?: string): string {
  const x = (r || "monthly").toLowerCase();
  if (x === "once" || x === "none") return "One-time / paused";
  if (x === "weekly") return "Weekly";
  if (x === "biweekly") return "Every 2 weeks";
  if (x === "quarterly") return "Every 3 months";
  if (x === "annual") return "Yearly";
  return "Monthly";
}

function toDateInput(v?: string | null): string {
  if (!v) return "";
  return String(v).slice(0, 10);
}

export function IncomeEditor({
  row,
  onDone,
  onCancel,
}: {
  row: IncomeRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateIncome(row.id, {
      source_name: String(fd.get("name")),
      amount: parseFloat(String(fd.get("amount"))),
      pay_frequency: String(fd.get("frequency")),
      next_pay_date: String(fd.get("next_pay")) || null,
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Field label="Source">
        <Input name="name" defaultValue={row.source_name} required />
      </Field>
      <Field label="Amount">
        <Input name="amount" type="number" defaultValue={row.amount} required />
      </Field>
      <Field label="Frequency">
        <Select name="frequency" defaultValue={row.pay_frequency}>
          <option value="weekly">Weekly</option>
          <option value="biweekly">Biweekly</option>
          <option value="monthly">Monthly</option>
        </Select>
      </Field>
      <Field label="Next pay">
        <Input name="next_pay" type="date" defaultValue={toDateInput(row.next_pay_date)} required />
      </Field>
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export function LiquidAccountEditor({
  row,
  onDone,
  onCancel,
}: {
  row: LiquidAccountRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [includeCashflow, setIncludeCashflow] = useState(row.include_in_cashflow);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateLiquidAccount(row.id, {
      account_name: String(fd.get("account_name")),
      account_type: String(fd.get("account_type")),
      institution: String(fd.get("institution") || ""),
      balance: parseFloat(String(fd.get("balance"))) || 0,
      current_apy: fd.get("current_apy") === "" ? null : Number(fd.get("current_apy")),
      apy_as_of: String(fd.get("apy_as_of") || "") || null,
      include_in_cashflow: includeCashflow,
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Field label="Name">
        <Input name="account_name" defaultValue={row.account_name} required />
      </Field>
      <Field label="Type">
        <Select name="account_type" defaultValue={row.account_type}>
          <option value="checking">Checking</option>
          <option value="savings">Savings</option>
        </Select>
      </Field>
      <Field label="Where is it held?">
        <Input name="institution" defaultValue={row.institution || ""} placeholder="Bank name" />
      </Field>
      <Field label="Balance">
        <Input name="balance" type="number" inputMode="decimal" defaultValue={row.balance} required />
      </Field>
      {(row.account_type === "savings" || row.account_type === "checking") && <>
        <Field label="Current APY" hint="Used only to compare your current yield with researched alternatives.">
          <Input name="current_apy" type="number" min="0" max="100" step="0.01" inputMode="decimal" defaultValue={row.current_apy ?? ""} placeholder="Optional" />
        </Field>
        <Field label="APY as of">
          <Input name="apy_as_of" type="date" defaultValue={toDateInput(row.apy_as_of)} />
        </Field>
      </>}
      <Toggle label="Include in cashflow" checked={includeCashflow} onChange={setIncludeCashflow} />
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export function BillEditor({
  row,
  onDone,
  onCancel,
}: {
  row: BillRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<BillMode>(
    row.recurrence === "scheduled" ? "scheduled" : row.recurrence === "once" || row.recurrence === "none" ? "once" : "recurring"
  );
  const [payments, setPayments] = useState<BillPayment[]>(
    row.payments?.map((payment) => ({ ...payment, amount: String(payment.amount) })) || [{ due_date: "", amount: "" }]
  );
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateBill(row.id, {
      name: String(fd.get("name")),
      ...billFormPaymentPayload(mode, Object.fromEntries(fd.entries()), payments),
      required: true,
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Field label="Name">
        <Input name="name" defaultValue={row.name} required />
      </Field>
      <BillTimingFields mode={mode} onModeChange={setMode} payments={payments} onPaymentsChange={setPayments}
        defaultAmount={row.amount} defaultDueDate={toDateInput(row.due_date)} defaultRecurrence={row.recurrence} />
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export function DebtEditor({
  row,
  onDone,
  onCancel,
}: {
  row: DebtRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [repeats, setRepeats] = useState((row.recurrence || "monthly") !== "once" && (row.recurrence || "monthly") !== "none");
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateDebt(row.id, {
      creditor: String(fd.get("creditor")),
      balance: parseFloat(String(fd.get("balance"))),
      apr: parseFloat(String(fd.get("apr"))),
      minimum_payment: parseFloat(String(fd.get("min"))),
      due_date: String(fd.get("due")) || null,
      debt_type: String(fd.get("debt_type")),
      recurrence: repeats ? String(fd.get("recurrence")) : "none",
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Toggle
        label="Repeats in monthly forecast"
        checked={repeats}
        onChange={setRepeats}
        hint="When off, this debt is excluded from cash-flow totals until you turn it back on."
      />
      <Field label="Creditor">
        <Input name="creditor" defaultValue={row.creditor} required />
      </Field>
      <Field label="Balance">
        <Input name="balance" type="number" defaultValue={row.balance} required />
      </Field>
      <Field label="APR %">
        <Input name="apr" type="number" defaultValue={row.apr} required />
      </Field>
      <Field label="Minimum payment">
        <Input name="min" type="number" defaultValue={row.minimum_payment} required />
      </Field>
      {repeats && (
        <Field label="Frequency">
          <Select
            name="recurrence"
            defaultValue={
              row.recurrence && row.recurrence !== "once" && row.recurrence !== "none"
                ? row.recurrence
                : "monthly"
            }
          >
            <option value="monthly">Monthly</option>
            <option value="weekly">Weekly</option>
            <option value="biweekly">Biweekly</option>
          </Select>
        </Field>
      )}
      <Field label="Payment due date">
        <Input name="due" type="date" defaultValue={toDateInput(row.due_date)} required={repeats} />
      </Field>
      <Field label="Type">
        <Select name="debt_type" defaultValue={row.debt_type}>
          <option value="credit_card">Credit card</option>
          <option value="loan">Loan</option>
          <option value="medical">Medical</option>
          <option value="other">Other</option>
        </Select>
      </Field>
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export function MortgageEditor({
  row,
  onDone,
  onCancel,
}: {
  row: MortgageRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [repeats, setRepeats] = useState((row.recurrence || "monthly") !== "once" && (row.recurrence || "monthly") !== "none");
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const balance = parseFloat(String(fd.get("balance")));
    await api.updateMortgage(row.id, {
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
      recurrence: repeats ? String(fd.get("recurrence")) : "none",
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Toggle
        label="Repeats in monthly forecast"
        checked={repeats}
        onChange={setRepeats}
        hint="When off, mortgage payment is excluded from forecast totals."
      />
      <Field label="Lender">
        <Input name="lender" defaultValue={row.lender} required />
      </Field>
      <Field label="Balance">
        <Input name="balance" type="number" defaultValue={row.current_balance} required />
      </Field>
      <Field label="Rate %">
        <Input name="rate" type="number" defaultValue={row.interest_rate} required />
      </Field>
      <Field label="Monthly P&amp;I">
        <Input name="payment" type="number" defaultValue={row.monthly_payment} required />
      </Field>
      <Field label="Escrow / month">
        <Input name="escrow" type="number" defaultValue={row.escrow_amount ?? 0} />
      </Field>
      {repeats && (
        <Field label="Frequency">
          <Select
            name="recurrence"
            defaultValue={
              row.recurrence && row.recurrence !== "once" && row.recurrence !== "none"
                ? row.recurrence
                : "monthly"
            }
          >
            <option value="monthly">Monthly</option>
          </Select>
        </Field>
      )}
      <Field label="Due by date">
        <Input
          name="due_by_date"
          type="date"
          defaultValue={toDateInput(row.due_by_date || row.start_date)}
          required={repeats}
        />
      </Field>
      <Field label="Late-fee deadline (grace ends)" hint="Keep the normal due date above. Use this date only if the plan needs the grace window to cover bills and daily essentials.">
        <Input name="late_by_date" type="date" defaultValue={toDateInput(row.late_by_date)} />
      </Field>
      <Field label="Property value">
        <Input name="value" type="number" defaultValue={row.property_value_estimate ?? ""} required />
      </Field>
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

type CreditCardRow = {
  id: string;
  issuer: string;
  card_name: string;
  balance: number;
  credit_limit: number;
  apr: number;
  minimum_payment: number;
  due_date?: string | null;
  recurrence?: string;
};

export function CreditCardEditor({
  row,
  onDone,
  onCancel,
}: {
  row: CreditCardRow;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [repeats, setRepeats] = useState(
    (row.recurrence || "monthly") !== "once" && (row.recurrence || "monthly") !== "none"
  );
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateCreditCard(row.id, {
      issuer: String(fd.get("issuer")),
      card_name: String(fd.get("card_name") || fd.get("issuer")),
      balance: parseFloat(String(fd.get("balance"))),
      credit_limit: parseFloat(String(fd.get("limit"))),
      apr: parseFloat(String(fd.get("apr"))),
      minimum_payment: parseFloat(String(fd.get("min"))),
      due_date: String(fd.get("due")) || null,
      recurrence: repeats ? String(fd.get("recurrence")) : "none",
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Toggle
        label="Repeats until paid off"
        checked={repeats}
        onChange={setRepeats}
        hint="Minimum payment is scheduled each period while balance remains."
      />
      <Field label="Issuer">
        <Input name="issuer" defaultValue={row.issuer} required />
      </Field>
      <Field label="Card name">
        <Input name="card_name" defaultValue={row.card_name} required />
      </Field>
      <Field label="Balance">
        <Input name="balance" type="number" defaultValue={row.balance} required />
      </Field>
      <Field label="Credit limit">
        <Input name="limit" type="number" defaultValue={row.credit_limit} required />
      </Field>
      <Field label="APR %">
        <Input name="apr" type="number" defaultValue={row.apr} required />
      </Field>
      <Field label="Minimum payment">
        <Input name="min" type="number" defaultValue={row.minimum_payment} required />
      </Field>
      {repeats && (
        <Field label="Frequency">
          <Select
            name="recurrence"
            defaultValue={
              row.recurrence && row.recurrence !== "once" && row.recurrence !== "none"
                ? row.recurrence
                : "monthly"
            }
          >
            <option value="monthly">Monthly</option>
            <option value="weekly">Weekly</option>
            <option value="biweekly">Biweekly</option>
          </Select>
        </Field>
      )}
      <Field label="Payment due date">
        <Input name="due" type="date" defaultValue={toDateInput(row.due_date)} required={repeats} />
      </Field>
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export type MiscRow = {
  id: string;
  name: string;
  category: string;
  category_label?: string;
  amount: number;
  due_date?: string | null;
  recurrence: string;
};

export function MiscSpendingEditor({
  row,
  catalog,
  onDone,
  onCancel,
}: {
  row: MiscRow;
  catalog: { id: string; label: string }[];
  onDone: () => void;
  onCancel: () => void;
}) {
  const [repeats, setRepeats] = useState(row.recurrence !== "once" && row.recurrence !== "none");
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateMiscSpending(row.id, {
      name: String(fd.get("name")),
      category: String(fd.get("category")),
      amount: parseFloat(String(fd.get("amount"))),
      due_date: String(fd.get("due")) || null,
      recurrence: repeats ? String(fd.get("recurrence")) : "once",
      repeats,
    });
    onDone();
  };
  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Field label="Name">
        <Input name="name" defaultValue={row.name} required />
      </Field>
      <Field label="Category">
        <Select name="category" defaultValue={row.category}>
          {catalog.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Amount">
        <Input name="amount" type="number" defaultValue={row.amount} required />
      </Field>
      <Toggle label="Repeats" checked={repeats} onChange={setRepeats} />
      {repeats && (
        <Field label="Frequency">
          <Select
            name="recurrence"
            defaultValue={row.recurrence === "once" ? "monthly" : row.recurrence}
          >
            <option value="monthly">Monthly</option>
            <option value="weekly">Weekly</option>
            <option value="biweekly">Biweekly</option>
            <option value="quarterly">Every 3 months</option>
            <option value="annual">Yearly</option>
          </Select>
        </Field>
      )}
      <Field label="Due / start date">
        <Input name="due" type="date" defaultValue={toDateInput(row.due_date)} required={repeats} />
      </Field>
      <div className="inline-edit-actions">
        <Btn type="button" variant="secondary" onClick={onCancel}>
          Cancel
        </Btn>
        <BtnSubmit>Save</BtnSubmit>
      </div>
    </form>
  );
}

export { recurrenceLabel };
