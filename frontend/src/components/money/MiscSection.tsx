import { FormEvent, useEffect, useState } from "react";
import { Btn, BtnSubmit, Card, EmptyState, Field, IconBtn, IconPlus, Input, ListRow, Select, Toggle } from "../ui";
import { api, type BudgetCategoryRow } from "../../api";
import { Link } from "react-router-dom";
import { MiscSpendingEditor, recurrenceLabel, type MiscRow } from "./RecordEditors";
import { BillTimingFields, billFormPaymentPayload, type BillMode, type BillPayment } from "./BillTimingFields";

const CATEGORY_LABELS: Record<string, string> = {
  daily_spending: "Daily spending",
  eating_out: "Eating out",
  entertainment: "Entertainment",
  other: "Other",
};

export function MiscSection({
  items,
  catalog,
  editingId,
  setEditingId,
  onChanged,
}: {
  items: MiscRow[];
  catalog: { id: string; label: string }[];
  editingId: string | null;
  setEditingId: (id: string | null) => void;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [repeats, setRepeats] = useState(false);
  const [budgets, setBudgets] = useState<BudgetCategoryRow[]>([]);
  const [addingBudget, setAddingBudget] = useState(false);
  const [editingBudgetId, setEditingBudgetId] = useState<string | null>(null);
  const [budgetError, setBudgetError] = useState("");
  const [movingId, setMovingId] = useState<string | null>(null);
  const [moveMode, setMoveMode] = useState<BillMode>("recurring");
  const [movePayments, setMovePayments] = useState<BillPayment[]>([{ due_date: "", amount: "" }]);
  const [moveError, setMoveError] = useState("");
  const [moveNotice, setMoveNotice] = useState("");

  const startMove = (row: MiscRow) => {
    setMovingId(row.id);
    setMoveMode(row.recurrence === "once" || row.recurrence === "none" ? "once" : "recurring");
    setMovePayments([{ due_date: row.due_date?.slice(0, 10) || "", amount: String(row.amount) }]);
    setMoveError("");
    setMoveNotice("");
  };

  const moveToBills = async (event: FormEvent<HTMLFormElement>, row: MiscRow) => {
    event.preventDefault();
    const raw = Object.fromEntries(new FormData(event.currentTarget).entries());
    setMoveError("");
    try {
      await api.moveMiscToBills(row.id, {
        name: String(raw.name || row.name),
        ...billFormPaymentPayload(moveMode, raw, movePayments),
        required: true,
      });
      setMovingId(null);
      setMoveNotice("Moved to Bills. The old spending entry is archived, so it will not be counted twice.");
      onChanged();
    } catch (error) {
      setMoveError(error instanceof Error ? error.message : "Could not move this payment.");
    }
  };

  const reloadBudgets = () => api.listBudgetCategories().then(setBudgets);
  useEffect(() => { void reloadBudgets().catch(() => setBudgetError("Could not load everyday spending.")); }, []);

  const saveBudget = async (event: FormEvent<HTMLFormElement>, row?: BudgetCategoryRow) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body = {
      name: String(data.get("name") || "").trim(),
      usual_amount: Number(data.get("usual_amount")),
      period: String(data.get("period") || "month") as "day" | "week" | "month",
      planned_weekday: Number(data.get("planned_weekday") ?? 5),
    };
    setBudgetError("");
    try {
      if (row) await api.updateBudgetCategory(row.id, body);
      else await api.createBudgetCategory(body);
      await reloadBudgets();
      setAddingBudget(false);
      setEditingBudgetId(null);
      onChanged();
    } catch (error) {
      setBudgetError(error instanceof Error ? error.message : "Could not save everyday spending.");
    }
  };

  const removeBudget = async (row: BudgetCategoryRow) => {
    setBudgetError("");
    try {
      await api.deleteBudgetCategory(row.id);
      await reloadBudgets();
      onChanged();
    } catch (error) {
      setBudgetError(error instanceof Error ? error.message : "Could not remove everyday spending.");
    }
  };

  const addSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.createMiscSpending({
      name: String(fd.get("name")),
      category: String(fd.get("category")),
      amount: parseFloat(String(fd.get("amount"))),
      due_date: String(fd.get("due")) || null,
      recurrence: repeats ? String(fd.get("recurrence")) : "once",
      repeats,
    });
    setAdding(false);
    setRepeats(false);
    onChanged();
  };

  return (
    <>
      <h4>Everyday spending to plan for</h4>
      <p className="field-hint">Plan for groceries, eating out, entertainment, and other everyday costs by day, week, or month. Ledger keeps this money out of “extra to spend.” Bills and payment plans belong under Bills.</p>
      {budgetError && <p className="error" role="alert">{budgetError}</p>}
      <div className="money-record-list">
        {budgets.map((row) => editingBudgetId === row.id ? (
          <form key={row.id} className="inline-edit-form" onSubmit={(event) => void saveBudget(event, row)}>
            <Field label="Spending type"><Input name="name" defaultValue={row.name} maxLength={80} required /></Field>
            <Field label="Usual amount"><Input name="usual_amount" type="number" min="0" max="1000000" step="0.01" inputMode="decimal" defaultValue={row.usual_amount ?? row.monthly_limit} required /></Field>
            <Field label="How often"><Select name="period" defaultValue={row.period || "month"}><option value="day">Per day</option><option value="week">Per week</option><option value="month">Per month</option></Select></Field>
            <Field label="Grocery trip day (for weekly groceries)"><Select name="planned_weekday" defaultValue={String(row.planned_weekday ?? 5)}>{["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, index) => <option key={day} value={index}>{day}</option>)}</Select></Field>
            <div className="inline-edit-actions"><Btn type="button" variant="secondary" onClick={() => setEditingBudgetId(null)}>Cancel</Btn><BtnSubmit>Save</BtnSubmit></div>
          </form>
        ) : (
          <ListRow key={row.id} title={row.name} subtitle={`Planned per ${row.period || "month"}`}
            trailing={`$${Number(row.usual_amount ?? row.monthly_limit).toLocaleString()}`}
            onEdit={() => setEditingBudgetId(row.id)} onDelete={() => void removeBudget(row)} />
        ))}
      </div>
      {addingBudget ? (
        <form className="inline-edit-form" onSubmit={(event) => void saveBudget(event)}>
          <Field label="Spending type"><Input name="name" placeholder="Groceries, fuel, household basics…" maxLength={80} required /></Field>
          <Field label="Usual amount"><Input name="usual_amount" type="number" min="0" max="1000000" step="0.01" inputMode="decimal" required /></Field>
          <Field label="How often"><Select name="period" defaultValue="week"><option value="day">Per day</option><option value="week">Per week</option><option value="month">Per month</option></Select></Field>
          <Field label="Grocery trip day (for weekly groceries)"><Select name="planned_weekday" defaultValue="5">{["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, index) => <option key={day} value={index}>{day}</option>)}</Select></Field>
          <div className="inline-edit-actions"><Btn type="button" variant="secondary" onClick={() => setAddingBudget(false)}>Cancel</Btn><BtnSubmit>Save</BtnSubmit></div>
        </form>
      ) : <Btn variant="secondary" onClick={() => setAddingBudget(true)}>Add everyday spending</Btn>}

      <h4>Other dated spending</h4>
      <p className="field-hint">
        Use this for a birthday, holiday, or other non-bill expense with a known date. Put subscriptions, utilities, and pay-in-3/pay-in-4 plans under <Link to="/money?section=bills">Bills</Link> instead. Use “Move to Bills” below for an existing item so it is not counted twice.
      </p>
      {moveNotice && <p role="status">{moveNotice} <Link to="/money?section=bills">Review Bills</Link></p>}

      {!adding ? (
        <IconBtn title="Add miscellaneous spending" onClick={() => setAdding(true)}>
          <IconPlus />
        </IconBtn>
      ) : (
        <div className="misc-add-wrap">
        <Card className="misc-add-form">
          <h4 style={{ marginTop: 0 }}>Add miscellaneous spending</h4>
          <form className="inline-edit-form" onSubmit={addSubmit}>
            <Field label="Name">
              <Input name="name" placeholder="Birthday gift, outing, hobby expense…" required />
            </Field>
            <Field label="Category">
              <Select name="category" defaultValue="eating_out">
                {catalog.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Amount">
              <Input name="amount" type="number" min="0" step="0.01" required />
            </Field>
            <Toggle label="Repeats" checked={repeats} onChange={setRepeats} />
            {repeats && (
              <Field label="Frequency">
                <Select name="recurrence" defaultValue="monthly">
                  <option value="monthly">Monthly</option>
                  <option value="weekly">Weekly</option>
                  <option value="biweekly">Biweekly</option>
                  <option value="quarterly">Every 3 months</option>
                  <option value="annual">Yearly</option>
                </Select>
              </Field>
            )}
            <Field label="Due / start date">
              <Input name="due" type="date" required={repeats} />
            </Field>
            <div className="inline-edit-actions">
              <Btn type="button" variant="secondary" onClick={() => setAdding(false)}>
                Cancel
              </Btn>
              <BtnSubmit>Add</BtnSubmit>
            </div>
          </form>
        </Card>
        </div>
      )}

      {!items.length && !adding ? (
        <EmptyState message="No miscellaneous spending yet." />
      ) : (
        <div className="money-record-list" style={{ marginTop: "0.75rem" }}>
          {items.map((row) => {
            const editKey = `misc:${row.id}`;
            const catLabel = CATEGORY_LABELS[row.category] || row.category;
            return movingId === row.id ? (
              <Card key={row.id}>
                <h4>Move to Bills</h4>
                <p className="field-hint">Enter only the payments still owed. Past spending history stays archived. For a purchase paid through PayPal, record the purchase once—not a second PayPal bill.</p>
                {moveError && <p className="error" role="alert">{moveError}</p>}
                <form className="inline-edit-form" onSubmit={(event) => void moveToBills(event, row)}>
                  <Field label="Bill or purchase name"><Input name="name" defaultValue={row.name} required /></Field>
                  <BillTimingFields mode={moveMode} onModeChange={setMoveMode} payments={movePayments} onPaymentsChange={setMovePayments}
                    defaultAmount={row.amount} defaultDueDate={row.due_date?.slice(0, 10)} defaultRecurrence={row.recurrence} />
                  <div className="inline-edit-actions"><Btn type="button" variant="secondary" onClick={() => setMovingId(null)}>Cancel</Btn><BtnSubmit>Move to Bills</BtnSubmit></div>
                </form>
              </Card>
            ) : editingId === editKey ? (
              <Card key={row.id}>
                <MiscSpendingEditor
                  row={row}
                  catalog={catalog}
                  onDone={onChanged}
                  onCancel={() => setEditingId(null)}
                />
              </Card>
            ) : (
              <div key={row.id}>
                <ListRow title={row.name}
                  subtitle={`${catLabel} · ${recurrenceLabel(row.recurrence)}${row.due_date ? ` · due ${String(row.due_date).slice(0, 10)}` : ""}`}
                  trailing={`$${row.amount.toLocaleString()}`}
                  trailTone="danger"
                  onEdit={() => setEditingId(editKey)}
                  onDelete={() => api.deleteMiscSpending(row.id).then(onChanged)} />
                <Btn type="button" variant="secondary" onClick={() => startMove(row)}>Move to Bills</Btn>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
