import { FormEvent, useState } from "react";
import { api } from "../../api";
import { Btn, BtnSubmit, Field, Input } from "../ui";

export function BillDeferralControl({
  paymentId,
  kind,
  originalDueDate,
  approvedDate,
  onSaved,
}: {
  paymentId: string;
  kind: "bill" | "debt" | "mortgage" | "credit_card";
  originalDueDate: string | null;
  approvedDate?: string | null;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState("");
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!originalDueDate) return null;
  if (approvedDate) return <p className="field-hint">Payee-approved date for this payment: <strong>{approvedDate}</strong>. Original due date: {originalDueDate}. This payment cannot be deferred again; future due dates stay unchanged.</p>;
  if (!open) return <Btn type="button" variant="secondary" onClick={() => setOpen(true)}>Record an approved one-time date</Btn>;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.approvePaymentDeferral(kind, paymentId, {
        original_due_date: originalDueDate,
        deferred_due_date: date,
        payee_approved: approved,
      });
      setOpen(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the new date");
    } finally {
      setBusy(false);
    }
  };
  return <form className="inline-edit-form" onSubmit={submit}>
    <p className="field-hint">Only use this after the payee confirms this payment can move. It changes this occurrence, not the bill's regular due date. Check fees and credit or service impacts with the payee.</p>
    <Field label="New approved payment date"><Input type="date" min={originalDueDate} value={date} onChange={(event) => setDate(event.target.value)} required /></Field>
    <label><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} required /> The payee approved this date for this payment.</label>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="inline-edit-actions"><Btn type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Btn><BtnSubmit disabled={busy}>Save approved date</BtnSubmit></div>
  </form>;
}
