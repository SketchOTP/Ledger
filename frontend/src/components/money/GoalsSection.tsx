import { FormEvent, useState } from "react";
import {
  Btn,
  BtnSubmit,
  Card,
  EmptyState,
  Field,
  IconBtn,
  IconPlus,
  Input,
  ListRow,
  Toggle,
} from "../ui";
import { api, type GoalAnalysis, type GoalPlan, type SavingsGoalRow } from "../../api";

function fmtMoney(n: number) {
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}

function trailTone(status: string): "default" | "danger" | "safe" {
  if (status === "complete" || status === "ready") return "safe";
  if (status === "blocked") return "danger";
  return "default";
}

function GoalEditor({
  goal,
  onSaved,
  onCancel,
}: {
  goal: SavingsGoalRow;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.updateSavingsGoal(goal.id, {
      name: String(fd.get("name")),
      target_amount: parseFloat(String(fd.get("target_amount"))),
      saved_amount: parseFloat(String(fd.get("saved_amount")) || "0"),
      due_date: String(fd.get("due_date")) || null,
      enabled: goal.enabled,
      notes: String(fd.get("notes") || ""),
    });
    onSaved();
  };

  return (
    <form className="inline-edit-form" onSubmit={submit}>
      <Field label="Goal name">
        <Input name="name" defaultValue={goal.name} required />
      </Field>
      <Field label="Target amount">
        <Input
          name="target_amount"
          type="number"
          min="0.01"
          step="0.01"
          defaultValue={goal.target_amount}
          required
        />
      </Field>
      <Field label="Saved so far">
        <Input name="saved_amount" type="number" min="0" step="0.01" defaultValue={goal.saved_amount} />
      </Field>
      <Field label="Due date (optional)">
        <Input name="due_date" type="date" defaultValue={goal.due_date?.slice(0, 10) ?? ""} />
      </Field>
      <Field label="Notes">
        <Input name="notes" defaultValue={goal.notes ?? ""} />
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

export function GoalsSection({
  goals,
  plan,
  editingId,
  setEditingId,
  onChanged,
}: {
  goals: SavingsGoalRow[];
  plan: GoalPlan | null;
  editingId: string | null;
  setEditingId: (id: string | null) => void;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const analysisById = new Map((plan?.goals ?? []).map((g) => [g.goal_id, g]));

  const toggleEnabled = async (goal: SavingsGoalRow) => {
    await api.toggleSavingsGoal(goal.id, !goal.enabled);
    onChanged();
  };

  const addSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    await api.createSavingsGoal({
      name: String(fd.get("name")),
      target_amount: parseFloat(String(fd.get("target_amount"))),
      saved_amount: parseFloat(String(fd.get("saved_amount")) || "0"),
      due_date: String(fd.get("due_date")) || null,
      enabled: true,
    });
    setAdding(false);
    onChanged();
  };

  return (
    <>
      <p className="field-hint">
        Optional savings targets. When enabled, Ledger recommends contributions only when bills,
        debt minimums, and your buffer are covered.
      </p>

      {plan && (
        <Card className="goal-plan-summary">
          <p style={{ margin: 0 }}>{plan.summary}</p>
          {plan.can_contribute && plan.total_recommended > 0 && (
            <p className="success-toast" style={{ margin: "0.35rem 0 0" }}>
              Safe to save ${fmtMoney(plan.total_recommended)} this period
            </p>
          )}
        </Card>
      )}

      {!adding ? (
        <IconBtn title="Add savings goal" onClick={() => setAdding(true)}>
          <IconPlus />
        </IconBtn>
      ) : (
        <Card className="goal-add-form">
          <h4 style={{ marginTop: 0 }}>Add savings goal</h4>
          <form className="inline-edit-form" onSubmit={addSubmit}>
            <Field label="Goal name">
              <Input name="name" placeholder="Save for college" required />
            </Field>
            <Field label="Target amount">
              <Input name="target_amount" type="number" min="0.01" step="0.01" required />
            </Field>
            <Field label="Saved so far">
              <Input name="saved_amount" type="number" min="0" step="0.01" defaultValue="0" />
            </Field>
            <Field label="Due date (optional)">
              <Input name="due_date" type="date" />
            </Field>
            <div className="inline-edit-actions">
              <Btn type="button" variant="secondary" onClick={() => setAdding(false)}>
                Cancel
              </Btn>
              <BtnSubmit>Add goal</BtnSubmit>
            </div>
          </form>
        </Card>
      )}

      {!goals.length && !adding ? (
        <EmptyState message="No savings goals yet. Add one like “Save for college” with a target and optional due date." />
      ) : (
        <div className="money-record-list" style={{ marginTop: "0.75rem" }}>
          {goals.map((goal) => {
            const editKey = `goal:${goal.id}`;
            const analysis = analysisById.get(goal.id) as GoalAnalysis | undefined;
            const pct =
              goal.target_amount > 0
                ? Math.min(100, Math.round((goal.saved_amount / goal.target_amount) * 100))
                : 0;
            const status = analysis?.status ?? (goal.enabled ? "ready" : "disabled");
            const trailing =
              analysis?.recommended_contribution && analysis.recommended_contribution > 0
                ? `$${fmtMoney(analysis.recommended_contribution)}`
                : `${pct}%`;

            return editingId === editKey ? (
              <Card key={goal.id}>
                <GoalEditor
                  goal={goal}
                  onSaved={() => {
                    setEditingId(null);
                    onChanged();
                  }}
                  onCancel={() => setEditingId(null)}
                />
              </Card>
            ) : (
              <div key={goal.id} className="goal-row-wrap">
                <div className="goal-row-toggle">
                  <Toggle
                    label={goal.enabled ? "Included" : "Off"}
                    checked={goal.enabled}
                    onChange={() => toggleEnabled(goal)}
                  />
                </div>
                <ListRow
                  title={goal.name}
                  subtitle={
                    `$${fmtMoney(goal.saved_amount)} / $${fmtMoney(goal.target_amount)}` +
                    (goal.due_date ? ` · due ${String(goal.due_date).slice(0, 10)}` : "") +
                    (analysis?.message ? ` · ${analysis.message.slice(0, 100)}` : "")
                  }
                  trailing={trailing}
                  trailTone={trailTone(status)}
                  onEdit={() => setEditingId(editKey)}
                  onDelete={() => api.deleteSavingsGoal(goal.id).then(onChanged)}
                />
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
