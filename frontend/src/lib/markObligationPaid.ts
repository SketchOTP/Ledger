import { api } from "../api";

export type PayableObligation = {
  due_date?: string;
  date?: string;
  category?: string;
  amount?: number;
  bill_id?: string | null;
  debt_id?: string | null;
  mortgage_id?: string | null;
  travel_expense_id?: string | null;
  misc_spending_id?: string | null;
  credit_card_id?: string | null;
  profile_id?: string | null;
  can_mark_paid?: boolean;
};

export async function markObligationPaid(item: PayableObligation): Promise<void> {
  const due = (item.due_date || item.date || "").slice(0, 10);
  if (!due) {
    throw new Error("Missing due date");
  }
  const body: Parameters<typeof api.markObligationPaid>[0] = { due_date: due };
  if (item.bill_id) body.bill_id = item.bill_id;
  else if (item.debt_id) body.debt_id = item.debt_id;
  else if (item.mortgage_id) body.mortgage_id = item.mortgage_id;
  else if (item.travel_expense_id) body.travel_expense_id = item.travel_expense_id;
  else if (item.misc_spending_id) body.misc_spending_id = item.misc_spending_id;
  else if (item.credit_card_id) body.credit_card_id = item.credit_card_id;
  else if (item.profile_id) body.profile_id = item.profile_id;
  else {
    throw new Error("This item cannot be marked paid here — edit it under My Money.");
  }
  if (item.amount != null && Number.isFinite(item.amount)) {
    body.amount = item.amount;
  }
  await api.markObligationPaid(body);
}

export function payableKey(item: PayableObligation & { name?: string }): string {
  const due = (item.due_date || item.date || "").slice(0, 10);
  if (item.bill_id) return `bill:${item.bill_id}:${due}`;
  if (item.debt_id) return `debt:${item.debt_id}:${due}`;
  if (item.mortgage_id) return `mortgage:${item.mortgage_id}:${due}`;
  if (item.travel_expense_id) return `travel:${item.travel_expense_id}:${due}`;
  if (item.misc_spending_id) return `misc:${item.misc_spending_id}:${due}`;
  if (item.credit_card_id) return `card:${item.credit_card_id}:${due}`;
  if (item.profile_id) return `rent:${item.profile_id}:${due}`;
  return `other:${item.name}:${due}`;
}

export function isPayable(item: PayableObligation): boolean {
  if (item.can_mark_paid === false) return false;
  const due = item.due_date || item.date;
  if (!due) return false;
  if (item.can_mark_paid === true) return true;
  return Boolean(
    item.bill_id ||
      item.debt_id ||
      item.mortgage_id ||
      item.travel_expense_id ||
      item.misc_spending_id ||
      item.credit_card_id ||
      item.profile_id
  );
}
