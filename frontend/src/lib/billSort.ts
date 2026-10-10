/** Next due soonest first; no due date at the bottom; stable tie-break by name. */

export type BillDueSortable = {
  name?: string;
  due_date?: string | null;
  next_due_date?: string | null;
};

function dueSortKey(row: BillDueSortable): number {
  const raw = row.next_due_date ?? row.due_date;
  if (!raw) return Number.MAX_SAFE_INTEGER;
  const t = Date.parse(String(raw).slice(0, 10));
  return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : t;
}

export function sortBillsByDueDate<T extends BillDueSortable>(bills: T[]): T[] {
  return [...bills].sort((a, b) => {
    const diff = dueSortKey(a) - dueSortKey(b);
    if (diff !== 0) return diff;
    return (a.name ?? "").localeCompare(b.name ?? "", undefined, { sensitivity: "base" });
  });
}
