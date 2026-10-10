import { Btn, Field, Input, Select } from "../ui";

export type BillMode = "recurring" | "once" | "scheduled";
export type BillPayment = { due_date: string; amount: string; paid?: boolean };

export function BillTimingFields({
  mode,
  onModeChange,
  payments,
  onPaymentsChange,
  defaultAmount,
  defaultDueDate,
  defaultRecurrence,
}: {
  mode: BillMode;
  onModeChange: (mode: BillMode) => void;
  payments: BillPayment[];
  onPaymentsChange: (payments: BillPayment[]) => void;
  defaultAmount?: number;
  defaultDueDate?: string | null;
  defaultRecurrence?: string;
}) {
  const hasPaid = payments.some((row) => row.paid);
  return (
    <>
      <Field label="Payment pattern">
        <Select value={mode} onChange={(event) => onModeChange(event.target.value as BillMode)} disabled={hasPaid}>
          <option value="recurring">Repeats regularly</option>
          <option value="once">One payment</option>
          <option value="scheduled">A set number of payments</option>
        </Select>
      </Field>
      {mode === "scheduled" ? (
        <>
          <p className="field-hint">Enter each payment's actual amount and due date. Use this for pay-in-4 or a three-payment plan; do not also enter the same charge under Everyday spending.</p>
          {payments.map((payment, index) => (
            <div className="inline-edit-form" key={index}>
              <Field label={`Payment ${index + 1} due date`}>
                <Input type="date" value={payment.due_date} disabled={payment.paid}
                  onChange={(event) => onPaymentsChange(payments.map((row, i) => i === index ? { ...row, due_date: event.target.value } : row))} required />
              </Field>
              <Field label={`Payment ${index + 1} amount`}>
                <Input type="number" min="0.01" max="1000000" step="0.01" inputMode="decimal" value={payment.amount} disabled={payment.paid}
                  onChange={(event) => onPaymentsChange(payments.map((row, i) => i === index ? { ...row, amount: event.target.value } : row))} required />
              </Field>
              {payment.paid ? <span className="field-hint">Paid</span> : payments.length > 1 ? (
                <Btn type="button" variant="secondary" onClick={() => onPaymentsChange(payments.filter((_, i) => i !== index))}>Remove payment</Btn>
              ) : null}
            </div>
          ))}
          {payments.length < 24 && (
            <Btn type="button" variant="secondary" onClick={() => onPaymentsChange([...payments, { due_date: "", amount: "" }])}>Add another payment</Btn>
          )}
        </>
      ) : (
        <>
          <Field label="Amount">
            <Input name="amount" type="number" min="0.01" max="1000000" step="0.01" inputMode="decimal" defaultValue={defaultAmount} required />
          </Field>
          {mode === "recurring" && (
            <Field label="Frequency">
              <Select name="recurrence" defaultValue={defaultRecurrence === "scheduled" || defaultRecurrence === "once" ? "monthly" : defaultRecurrence || "monthly"}>
                <option value="monthly">Monthly</option>
                <option value="weekly">Weekly</option>
                <option value="biweekly">Every 2 weeks</option>
                <option value="quarterly">Every 3 months</option>
                <option value="annual">Yearly</option>
              </Select>
            </Field>
          )}
          <Field label="Due date">
            <Input name="due" type="date" defaultValue={defaultDueDate || ""} required />
          </Field>
        </>
      )}
    </>
  );
}

export function billFormPaymentPayload(mode: BillMode, raw: Record<string, unknown>, payments: BillPayment[]) {
  if (mode === "scheduled") {
    const rows = payments.map((row) => ({ due_date: row.due_date, amount: Number(row.amount) }));
    return {
      amount: rows[0]?.amount || 0,
      due_date: rows[0]?.due_date || null,
      recurrence: "scheduled",
      repeats: false,
      payments: rows,
    };
  }
  return {
    amount: Number(raw.amount),
    due_date: String(raw.due || "") || null,
    recurrence: mode === "once" ? "once" : String(raw.recurrence || "monthly"),
    repeats: mode === "recurring",
    payments: null,
  };
}
