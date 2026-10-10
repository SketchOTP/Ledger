import { api } from "../api";

export type ReceivableIncome = {
  due_date?: string;
  date?: string;
  pay_date?: string;
  amount?: number;
  income_source_id?: string | null;
  can_mark_received?: boolean;
};

export async function markIncomeReceived(item: ReceivableIncome): Promise<void> {
  const pay = (item.pay_date || item.due_date || item.date || "").slice(0, 10);
  if (!pay) {
    throw new Error("Missing pay date");
  }
  if (!item.income_source_id) {
    throw new Error("This income item cannot be marked received here — edit it under My Money.");
  }
  const body: Parameters<typeof api.markIncomeReceived>[0] = {
    income_source_id: item.income_source_id,
    pay_date: pay,
  };
  if (item.amount != null && Number.isFinite(item.amount)) {
    body.amount = item.amount;
  }
  await api.markIncomeReceived(body);
}

export function receivableKey(item: ReceivableIncome & { name?: string }): string {
  const pay = (item.pay_date || item.due_date || item.date || "").slice(0, 10);
  return `income:${item.income_source_id}:${pay}`;
}

export function isReceivable(item: ReceivableIncome): boolean {
  if (item.can_mark_received === false) return false;
  const pay = item.pay_date || item.due_date || item.date;
  if (!pay || !item.income_source_id) return false;
  return item.can_mark_received === true || Boolean(item.income_source_id);
}
