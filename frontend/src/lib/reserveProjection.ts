/** Illustrative earmarks on the existing cash-flow forecast, not transfers. */
export interface ReserveSnapshot {
  allowanceProjection?: ReservePoint[];
  cushionCurrent: number | null;
  cushionGoal: number;
  suggestedRefill30Days: number;
  nearTermSurprises: number;
  uncommittedNow: number | null;
  extraSpendNow: number | null;
  extraSpendShare: number;
  usualSpending30Days: Array<{ date: string; amount: number }>;
}

export interface ReservePoint {
  date: string;
  balanceAfterSpending: number;
  cushion: number | null;
  surprises: number;
  uncommitted: number | null;
  extraSpend: number | null;
}

export function projectReserveLines(
  days: Array<{ date: string; balance: number }>,
  snapshot: ReserveSnapshot,
): ReservePoint[] {
  if (!days.length) return [];
  if (snapshot.allowanceProjection) {
    const recorded = new Map(snapshot.allowanceProjection.map(row => [row.date, row]));
    return days.map(day => recorded.get(day.date) ?? {
      date: day.date, balanceAfterSpending: day.balance, cushion: null,
      surprises: 0, uncommitted: null, extraSpend: null,
    });
  }
  const surpriseTarget = Math.max(0, snapshot.nearTermSurprises);
  const refill = Math.max(0, snapshot.suggestedRefill30Days);
  const goal = Math.max(0, snapshot.cushionGoal);
  let cushion = snapshot.cushionCurrent == null || goal === 0
    ? null : Math.min(goal, Math.max(0, snapshot.cushionCurrent));
  const scheduledSpending = new Map<string, number>();
  for (const row of snapshot.usualSpending30Days) {
    scheduledSpending.set(row.date, (scheduledSpending.get(row.date) ?? 0) + Math.max(0, row.amount));
  }
  const plannedDates = [...scheduledSpending.keys()].sort();
  const lastPlannedDate = plannedDates[plannedDates.length - 1] ?? days[0].date;
  const plannedDays = Math.max(1, Math.round((Date.parse(lastPlannedDate) - Date.parse(days[0].date)) / 86400000) + 1);
  const averageAfter30Days = snapshot.usualSpending30Days.length
    ? [...scheduledSpending.values()].reduce((sum, amount) => sum + amount, 0) /
      plannedDays : 0;
  let spent = 0;
  const adjustedBalances = days.map((day) => {
    // The original graph does not deduct everyday spending. Use the owner's
    // dated 30-day plan first, then repeat its daily average as an illustration.
    spent += day.date <= lastPlannedDate
      ? (scheduledSpending.get(day.date) ?? 0) : averageAfter30Days;
    return day.balance - spent;
  });
  const lowestAhead = (index: number) => Math.min(...adjustedBalances.slice(index, index + Math.max(183, plannedDays)));
  const initialFloor = Math.max(cushion ?? 0, Math.min(surpriseTarget, adjustedBalances[0]));
  const initialUncommitted = Math.max(0, snapshot.uncommittedNow ?? 0);
  const initialExtra = Math.max(0, snapshot.extraSpendNow ?? 0);
  const initialPool = initialUncommitted + initialExtra;
  const extraShare = Math.max(0, Math.min(1, snapshot.extraSpendShare));
  // Preserve the other commitments embedded in today's 30-day calculation.
  // A rolling 30-day low prevents a paycheck from appearing free just before
  // a bill comes due, while later projected surplus can grow this virtual pot.
  const otherCommitted = Math.max(0, lowestAhead(0) - initialFloor - initialPool);
  let refillNotAlreadyInFloor = 0;

  return days.map((day, index) => {
    const adjustedCash = Math.max(0, adjustedBalances[index]);
    if (cushion != null) cushion = Math.min(cushion, adjustedCash);
    const surprises = Math.min(surpriseTarget, adjustedCash);
    const rollingLow = lowestAhead(index);
    const available = () => Math.max(0,
      rollingLow - Math.max(cushion ?? 0, surprises) - otherCommitted - refillNotAlreadyInFloor,
    );
    // Reapply only the small 30-day suggestion when the next dated window
    // leaves room. This is a what-if earmark, never a transfer or bank balance.
    if (cushion != null && refill > 0 && index > 0 && index % 30 === 0) {
      const step = Math.min(refill, goal - cushion, available() * 0.10);
      const floorBefore = Math.max(cushion, surprises);
      cushion = Math.min(goal, cushion + Math.max(0, step));
      // A contribution reduces optional room exactly once, including when the
      // existing surprise floor is larger than the cushion balance.
      refillNotAlreadyInFloor += Math.max(0, step - (Math.max(cushion, surprises) - floorBefore));
    }

    // Cushion and near-term surprise protection overlap; do not add them.
    // Protect today's uncommitted pot first. Split only additional headroom
    // using the owner's free-spend preference; do not add these two lines.
    const pool = available();
    const uncommitted = snapshot.uncommittedNow == null ? null : Math.min(
      pool, initialUncommitted + Math.max(0, pool - initialPool) * (1 - extraShare),
    );
    const extraSpend = snapshot.extraSpendNow == null || uncommitted == null
      ? null : Math.max(0, pool - uncommitted);
    return { date: day.date, balanceAfterSpending: adjustedBalances[index], cushion, surprises, uncommitted, extraSpend };
  });
}
