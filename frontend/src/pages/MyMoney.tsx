import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CashProfileForm } from "../components/money/CashProfileForm";
import { BillDeferralControl } from "../components/money/BillDeferralControl";
import { HousingForm } from "../components/money/HousingForm";
import { GoalsSection } from "../components/money/GoalsSection";
import { MiscSection } from "../components/money/MiscSection";
import { MoneyOverview } from "../components/money/MoneyOverview";
import {
  BillEditor,
  CreditCardEditor,
  DebtEditor,
  IncomeEditor,
  MortgageEditor,
  recurrenceLabel,
  type MiscRow,
} from "../components/money/RecordEditors";
import { QuickAddForm } from "../components/entry/QuickAddForms";
import {
  Card,
  CsvExportButton,
  CsvImportButton,
  EmptyState,
  Field,
  IconBtn,
  IconClose,
  IconPlus,
  Input,
  ListRow,
  PageHeader,
  Segmented,
} from "../components/ui";
import { api, type GoalPlan, type MonthlyObligations, type MyMoneySection, type MyMoneySummary, type SavingsGoalRow } from "../api";
import {
  MONEY_SECTION_IDS,
  moneySectionHref,
  moneySectionLabel,
  resolveMoneySection,
  type MoneySectionId,
} from "../lib/moneyNav";
import { sortBillsByDueDate } from "../lib/billSort";
import { moneyGuidance, plainSectionSummary } from "../lib/plainLanguage";

const QUICK_ADD_KIND: Record<string, Parameters<typeof QuickAddForm>[0]["kind"]> = {
  cash_on_hand: "cash_balance",
  income: "paycheck",
  bills: "bill",
  debts: "debt",
  mortgage: "mortgage",
  credit_cards: "credit_card",
};

const CSV_KINDS: Partial<Record<string, string>> = {
  bills: "bills",
  debts: "debts",
  credit_cards: "credit_cards",
};

const SEGMENT_OPTIONS = MONEY_SECTION_IDS.map((id) => ({
  value: id,
  label: moneySectionLabel(id),
}));

type IncomeRow = {
  id: string;
  source_name: string;
  amount: number;
  pay_frequency: string;
  next_pay_date?: string | null;
};

type BillRow = {
  id: string;
  name: string;
  amount: number;
  due_date?: string | null;
  next_due_date?: string | null;
  approved_deferral?: { original_due_date: string; deferred_due_date: string; approved_at: string } | null;
  recurrence: string;
  payments?: { due_date: string; amount: number; paid?: boolean }[];
  unpaid_past_due?: { due_date: string; amount: number }[];
  one_tap?: { due_date: string; amount: number } | null;
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
  unpaid_past_due?: { due_date: string; amount: number }[];
  one_tap?: { due_date: string; amount: number } | null;
  approved_deferral?: { original_due_date: string; deferred_due_date: string } | null;
};

export default function MyMoney() {
  const [params, setParams] = useSearchParams();
  const urlSection = resolveMoneySection(params);
  const [active, setActive] = useState<MoneySectionId>(urlSection ?? "overview");
  const [summary, setSummary] = useState<MyMoneySummary | null>(null);
  const [openAdd, setOpenAdd] = useState(false);
  const [csvMsg, setCsvMsg] = useState("");
  const [err, setErr] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);

  const [income, setIncome] = useState<IncomeRow[]>([]);
  const [bills, setBills] = useState<BillRow[]>([]);
  const [debts, setDebts] = useState<DebtRow[]>([]);
  const [mortgages, setMortgages] = useState<{ account: Record<string, unknown>; approved_deferral?: { original_due_date: string; deferred_due_date: string } | null; next_due_date?: string | null }[]>([]);
  const [cards, setCards] = useState<{ card: Record<string, unknown>; editable?: boolean; approved_deferral?: { original_due_date: string; deferred_due_date: string } | null; next_due_date?: string | null }[]>([]);
  const [borrower, setBorrower] = useState<{
    checking_balance?: number;
    emergency_fund_balance?: number;
    housing_status?: string;
    monthly_rent?: number;
  } | null>(null);
  const [accounts, setAccounts] = useState<
    { id: string; account_name: string; account_type: string; institution?: string; balance: number }[]
  >([]);
  const [miscItems, setMiscItems] = useState<MiscRow[]>([]);
  const [miscCatalog, setMiscCatalog] = useState<{ id: string; label: string }[]>([]);
  const [savingsGoals, setSavingsGoals] = useState<SavingsGoalRow[]>([]);
  const [goalPlan, setGoalPlan] = useState<GoalPlan | null>(null);
  const [showOptionalSections, setShowOptionalSections] = useState(false);

  const loadCoreRecords = useCallback(async () => {
    const [i, b, d, m, c, br, ac, goalsRes] = await Promise.all([
      api.incomeSources(),
      api.bills(),
      api.debts(),
      api.mortgages(),
      api.creditCards(),
      api.borrower(),
      api.liquidAccounts(),
      api.savingsGoals(),
    ]);
    setIncome(i as IncomeRow[]);
    setBills(sortBillsByDueDate(b as BillRow[]));
    setDebts(d as DebtRow[]);
    setMortgages(m as typeof mortgages);
    setCards(c as typeof cards);
    setBorrower((br as typeof borrower) || null);
    setAccounts((ac as typeof accounts) || []);
    setSavingsGoals(goalsRes.goals || []);
    setGoalPlan(goalsRes.plan || null);
  }, []);

  const loadDetailRecords = useCallback(async () => {
    if (active === "misc") {
      const misc = await api.miscSpending();
      setMiscItems(
        ((misc.items as MiscRow[]) || []).map((row) => ({
          ...row,
          category_label: misc.catalog?.find((category) => category.id === row.category)?.label ?? row.category,
        }))
      );
      setMiscCatalog(misc.catalog || []);
    }
  }, [active]);

  const load = useCallback(async () => {
    try {
      const [s] = await Promise.all([api.myMoneySummary(), loadCoreRecords(), loadDetailRecords()]);
      setSummary(s);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load");
    }
  }, [loadCoreRecords, loadDetailRecords]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (urlSection) {
      setActive(urlSection);
      setOpenAdd(false);
      setEditingId(null);
    }
  }, [urlSection]);

  const setSection = (id: MoneySectionId) => {
    setActive(id);
    setOpenAdd(false);
    setEditingId(null);
    setParams({ section: id }, { replace: true });
  };

  const sectionsById = useMemo(() => {
    const map = new Map<string, MyMoneySection>();
    summary?.sections.forEach((s) => map.set(s.id, s));
    return map;
  }, [summary]);

  const sec = sectionsById.get(active);

  const sectionOptions = useMemo(() => {
    const present = new Set<string>(["overview", "cash_on_hand", "income", "bills", "misc"]);
    if ((summary?.counts.debts ?? debts.length) > 0) present.add("debts");
    if ((summary?.counts.mortgages ?? mortgages.length) > 0 || (borrower?.housing_status || "rent").toLowerCase() !== "rent") present.add("mortgage");
    if ((summary?.counts.credit_cards ?? cards.length) > 0) present.add("credit_cards");
    if ((summary?.counts.savings_goals ?? savingsGoals.length) > 0) present.add("goals");
    // Keep a deep-linked optional editor usable, while the default view stays
    // focused on sections that actually exist for this household.
    if (active !== "overview" && !present.has(active)) present.add(active);
    const ids = showOptionalSections ? MONEY_SECTION_IDS : MONEY_SECTION_IDS.filter((id) => present.has(id));
    return ids.map((id) => ({ value: id, label: moneySectionLabel(id) }));
  }, [active, borrower?.housing_status, cards.length, debts.length, miscItems.length, mortgages.length, savingsGoals.length, showOptionalSections, summary]);

  const importCsv = async (kind: string, file: File) => {
    const content = await file.text();
    const result = await api.importCsv(kind, content);
    setCsvMsg(`Imported ${result.created} row(s). ${result.errors.length ? result.errors.join(" ") : ""}`);
    load();
  };

  const exportCsv = (kind: string) => {
    window.open(`/api/export/${kind}`, "_blank");
  };

  const afterSave = () => {
    setOpenAdd(false);
    setEditingId(null);
    load();
  };

  return (
    <>
      <PageHeader
        title="Money"
        subtitle="Your income, bills, money, and debts in one place."
      />

      {err && <p className="error">{err}</p>}
      {csvMsg && <p className="success-toast">{csvMsg}</p>}

      <div className="my-money-section-nav">
        <Segmented options={sectionOptions} value={active} onChange={setSection} />
        {!showOptionalSections && sectionOptions.length < SEGMENT_OPTIONS.length && (
          <button type="button" className="text-button" onClick={() => setShowOptionalSections(true)}>
            Add another kind of money
          </button>
        )}
      </div>

      {active === "overview" && (
        <MoneyOverview
          borrower={borrower}
          accounts={accounts}
          income={income}
          bills={bills}
          debts={debts}
          mortgages={mortgages}
          cards={cards}
          miscItems={miscItems}
          onEditSection={setSection}
        />
      )}

      {active !== "overview" && sec && (
        <div id={`section-${sec.id}`}>
        <Card className="money-section-card focused">
          <div className="money-section-head">
            <div>
              <h3>{sec.label}</h3>
              <p className="field-hint" style={{ margin: 0 }}>{plainSectionSummary(sec.id, sec.summary)}</p>
            </div>
          </div>

          {moneyGuidance(sec.id, sec.missing_fields) && (
            <p className="validation-warnings" style={{ margin: "0.5rem 0" }}>
              {moneyGuidance(sec.id, sec.missing_fields)}
            </p>
          )}

          {summary?.monthly_obligations && (
            <MonthlyObligationsNote sectionId={sec.id} obligations={summary.monthly_obligations} />
          )}

          {sec.id === "cash_on_hand" && <CashProfileForm onSaved={load} />}

          {sec.id === "mortgage" && (
            <Card className="housing-section-card">
              <HousingForm onSaved={load} />
            </Card>
          )}

          {sec.id === "misc" && (
            <MiscSection
              items={miscItems}
              catalog={miscCatalog}
              editingId={editingId}
              setEditingId={setEditingId}
              onChanged={load}
            />
          )}

          {sec.id === "goals" && (
            <GoalsSection
              goals={savingsGoals}
              plan={goalPlan}
              editingId={editingId}
              setEditingId={setEditingId}
              onChanged={load}
            />
          )}

          {sec.id !== "misc" && sec.id !== "goals" && (
            <SectionRecords
              sectionId={sec.id}
              income={income}
              bills={bills}
              debts={debts}
              mortgages={mortgages}
              cards={cards}
              editingId={editingId}
              setEditingId={setEditingId}
              onChanged={load}
            />
          )}

          {sec.id !== "cash_on_hand" &&
            sec.id !== "mortgage" &&
            sec.id !== "misc" &&
            sec.id !== "goals" && (
            <div className="money-section-actions">
              <IconBtn title={openAdd ? "Close form" : "Add new"} onClick={() => setOpenAdd(!openAdd)}>
                {openAdd ? <IconClose /> : <IconPlus />}
              </IconBtn>
              {CSV_KINDS[sec.id] && (
                <>
                  <CsvImportButton
                    onFile={(f) => importCsv(CSV_KINDS[sec.id]!, f)}
                  />
                  <CsvExportButton onClick={() => exportCsv(CSV_KINDS[sec.id]!)} />
                </>
              )}
            </div>
          )}

          {openAdd && QUICK_ADD_KIND[sec.id] && sec.id !== "cash_on_hand" && (
            <QuickAddForm
              kind={QUICK_ADD_KIND[sec.id]!}
              onSaved={afterSave}
              onCancel={() => setOpenAdd(false)}
            />
          )}
        </Card>
        </div>
      )}

      <p className="field-hint" style={{ textAlign: "center", marginTop: "1rem" }}>
        <Link to={moneySectionHref(active)}>Link to this section</Link>
      </p>
    </>
  );
}

function MonthlyObligationsNote({
  sectionId,
  obligations,
}: {
  sectionId: string;
  obligations: MonthlyObligations;
}) {
  const { bills, debt_minimums, mortgage_payments, credit_card_minimums, misc, total } =
    obligations;

  if (sectionId === "bills") {
    return (
      <p className="obligation-note">
        Repeating bills: <strong>${bills.toLocaleString()}/mo</strong>. Ledger already includes debt, card,
        and mortgage payments in your <strong>${total.toLocaleString()}/mo</strong> plan.
      </p>
    );
  }
  if (sectionId === "debts") {
    return (
      <p className="obligation-note">
        Debt payments: <strong>${debt_minimums.toLocaleString()}/mo</strong>. Ledger already includes these
        payments in your <strong>${total.toLocaleString()}/mo</strong> plan.
      </p>
    );
  }
  if (sectionId === "mortgage") {
    return (
      <p className="obligation-note">
        Mortgage payment: <strong>${mortgage_payments.toLocaleString()}/mo</strong>. Ledger already includes
        it in your <strong>${total.toLocaleString()}/mo</strong> plan.
      </p>
    );
  }
  if (sectionId === "credit_cards") {
    return (
      <p className="obligation-note">
        Card payments: <strong>${credit_card_minimums.toLocaleString()}/mo</strong>. Ledger already includes
        them in your <strong>${total.toLocaleString()}/mo</strong> plan.
      </p>
    );
  }
  if (sectionId === "misc") {
    const miscAmt = obligations.misc ?? 0;
    return (
      <p className="obligation-note">
        Miscellaneous spending: <strong>${miscAmt.toLocaleString()}/mo</strong> — eating out, entertainment,
        daily spending, and other flexible costs. Included in your monthly forecast total of{" "}
        <strong>${total.toLocaleString()}</strong>.
      </p>
    );
  }
  return null;
}

function SectionRecords({
  sectionId,
  income,
  bills,
  debts,
  mortgages,
  cards,
  editingId,
  setEditingId,
  onChanged,
}: {
  sectionId: string;
  income: IncomeRow[];
  bills: BillRow[];
  debts: DebtRow[];
  mortgages: { account: Record<string, unknown>; approved_deferral?: { original_due_date: string; deferred_due_date: string } | null; next_due_date?: string | null }[];
  cards: { card: Record<string, unknown>; editable?: boolean; approved_deferral?: { original_due_date: string; deferred_due_date: string } | null; next_due_date?: string | null }[];
  editingId: string | null;
  setEditingId: (id: string | null) => void;
  onChanged: () => void;
}) {
  if (sectionId === "income") {
    if (!income.length) {
      return <EmptyState message="No paychecks yet. Add one below." />;
    }
    return (
      <div className="money-record-list">
        {income.map((row) =>
          editingId === row.id ? (
            <Card key={row.id}>
              <IncomeEditor row={row} onDone={onChanged} onCancel={() => setEditingId(null)} />
            </Card>
          ) : (
            <ListRow
              key={row.id}
              title={row.source_name}
              subtitle={`${row.pay_frequency}${row.next_pay_date ? ` · next ${String(row.next_pay_date).slice(0, 10)}` : ""}`}
              trailing={`$${row.amount.toLocaleString()}`}
              trailTone="safe"
              onEdit={() => setEditingId(row.id)}
              onDelete={() => api.deleteIncome(row.id).then(onChanged)}
            />
          )
        )}
      </div>
    );
  }

  if (sectionId === "bills") {
    if (!bills.length) {
      return <EmptyState message="No bills yet." />;
    }
    return (
      <div className="money-record-list">
        {bills.map((row) => {
          const pastCount = row.unpaid_past_due?.length ?? 0;
          const subtitleParts = [
            row.recurrence === "scheduled" ? `${row.payments?.length || 0} scheduled payments` : row.recurrence,
            row.next_due_date ? `next payment ${String(row.next_due_date).slice(0, 10)}` : "",
            pastCount > 0 ? `${pastCount} older cycle(s) unpaid` : "",
          ].filter(Boolean);
          return editingId === row.id ? (
            <Card key={row.id}>
              <BillEditor row={row} onDone={onChanged} onCancel={() => setEditingId(null)} />
            </Card>
          ) : (
            <div key={row.id}>
            <ListRow
              title={row.name}
              subtitle={subtitleParts.join(" · ")}
              trailing={row.next_due_date ? `$${row.amount.toLocaleString()}` : undefined}
              trailTone="danger"
              markPaidLabel="$"
              onMarkPaid={
                row.one_tap
                  ? () => api.markBillPaidQuick(row.id).then(onChanged)
                  : undefined
              }
              onEdit={() => setEditingId(row.id)}
              onDelete={() => api.deleteBill(row.id).then(onChanged)}
            />
            <BillDeferralControl kind="bill" paymentId={row.id} originalDueDate={row.due_date ?? null} approvedDate={row.approved_deferral?.deferred_due_date} onSaved={onChanged} />
            </div>
          );
        })}
      </div>
    );
  }

  if (sectionId === "debts") {
    if (!debts.length) {
      return <EmptyState message="No debts listed. Add one or mark debt-free in the setup wizard." />;
    }
    return (
      <div className="money-record-list">
        {debts.map((row) => {
          const editKey = `debt:${row.id}`;
          const pastCount = row.unpaid_past_due?.length ?? 0;
          const subtitleParts = [
            recurrenceLabel(row.recurrence),
            `$${row.minimum_payment.toLocaleString()}/pmt`,
            `${row.apr}% APR`,
            row.due_date ? `next due ${String(row.due_date).slice(0, 10)}` : "",
            pastCount > 0 ? `${pastCount} older cycle(s) unpaid` : "",
          ].filter(Boolean);
          return editingId === editKey ? (
            <Card key={row.id}>
              <DebtEditor row={row} onDone={onChanged} onCancel={() => setEditingId(null)} />
            </Card>
          ) : (
            <div key={row.id}>
            <ListRow
              title={row.creditor}
              subtitle={subtitleParts.join(" · ")}
              trailing={`$${row.minimum_payment.toLocaleString()}/mo · bal $${row.balance.toLocaleString()}`}
              trailTone="danger"
              markPaidLabel="$"
              onMarkPaid={
                row.one_tap
                  ? () => api.markDebtPaidQuick(row.id).then(onChanged)
                  : undefined
              }
              onEdit={() => setEditingId(editKey)}
              onDelete={() => api.deleteDebt(row.id).then(onChanged)}
            />
            {!((row.debt_type === "card" || row.debt_type === "credit_card") && cards.some(
              (item) => String(item.card.issuer ?? "").toLowerCase() === row.creditor.toLowerCase()
            )) && <BillDeferralControl kind="debt" paymentId={row.id} originalDueDate={row.due_date ?? null} approvedDate={row.approved_deferral?.deferred_due_date} onSaved={onChanged} />}
            </div>
          );
        })}
      </div>
    );
  }

  if (sectionId === "mortgage") {
    if (!mortgages.length) {
      return null;
    }
    return (
      <div className="money-record-list" style={{ marginTop: "0.5rem" }}>
        {mortgages.map((m) => {
          const a = m.account as Record<string, unknown>;
          const id = String(a.id);
          const editKey = `mortgage:${id}`;
          const row = {
            id,
            lender: String(a.lender),
            current_balance: Number(a.current_balance),
            interest_rate: Number(a.interest_rate),
            monthly_payment: Number(a.monthly_payment),
            escrow_amount: Number(a.escrow_amount ?? 0),
            start_date: a.start_date as string | null,
            due_by_date: (a.due_by_date || a.start_date) as string | null,
            late_by_date: a.late_by_date as string | null,
            property_value_estimate: a.property_value_estimate as number | null,
            recurrence: String(a.recurrence || "monthly"),
          };
          const totalPmt = row.monthly_payment + row.escrow_amount;
          const dueStr = row.due_by_date ? String(row.due_by_date).slice(0, 10) : "";
          const lateStr = row.late_by_date ? String(row.late_by_date).slice(0, 10) : "";
          const when = m.approved_deferral
            ? `regular due ${dueStr} · this payment approved for ${m.approved_deferral.deferred_due_date}`
            : dueStr && lateStr
              ? `due by ${dueStr} · grace to ${lateStr}`
              : dueStr
                ? `due by ${dueStr}`
                : "";
          return editingId === editKey ? (
            <Card key={id}>
              <MortgageEditor row={row} onDone={onChanged} onCancel={() => setEditingId(null)} />
            </Card>
          ) : (
            <div key={id}>
            <ListRow
              title={row.lender}
              subtitle={`${recurrenceLabel(row.recurrence)} · ${row.interest_rate}% · $${totalPmt.toLocaleString()}/pmt${when ? ` · ${when}` : ""}`}
              trailing={`$${totalPmt.toLocaleString()}/mo · bal $${row.current_balance.toLocaleString()}`}
              trailTone="danger"
              onEdit={() => setEditingId(editKey)}
              onDelete={() => api.deleteMortgage(id).then(onChanged)}
            />
            <BillDeferralControl kind="mortgage" paymentId={id} originalDueDate={row.due_by_date} approvedDate={m.approved_deferral?.deferred_due_date} onSaved={onChanged} />
            </div>
          );
        })}
      </div>
    );
  }

  if (sectionId === "credit_cards") {
    if (!cards.length) {
      return <EmptyState message="No credit cards yet." />;
    }
    return (
      <div className="money-record-list">
        {cards.map((item, i) => {
          const c = item.card as Record<string, unknown>;
          const id = String(c.id ?? i);
          const editable = item.editable !== false;
          const row = {
            id,
            issuer: String(c.issuer ?? ""),
            card_name: String(c.card_name ?? c.issuer ?? ""),
            balance: Number(c.balance ?? 0),
            credit_limit: Number(c.credit_limit ?? 0),
            apr: Number(c.apr ?? 0),
            minimum_payment: Number(c.minimum_payment ?? 0),
            due_date: c.due_date as string | null,
            recurrence: String(c.recurrence || "monthly"),
          };
          const editKey = `card-${id}`;
          return editingId === editKey && editable ? (
            <Card key={id}>
              <CreditCardEditor row={row} onDone={onChanged} onCancel={() => setEditingId(null)} />
            </Card>
          ) : (
            <div key={id}>
            <ListRow
              title={`${row.issuer} — ${row.card_name}`}
              subtitle={`${recurrenceLabel(row.recurrence)} · ${row.apr}% · min $${row.minimum_payment.toLocaleString()}/pmt${item.next_due_date ? ` · next payment ${item.next_due_date}` : ""}`}
              trailing={`$${row.minimum_payment.toLocaleString()}/mo · bal $${row.balance.toLocaleString()}`}
              trailTone="danger"
              onEdit={editable ? () => setEditingId(editKey) : undefined}
              onDelete={editable ? () => api.deleteCreditCard(id).then(onChanged) : undefined}
            />
            {editable && <BillDeferralControl kind="credit_card" paymentId={id} originalDueDate={row.due_date} approvedDate={item.approved_deferral?.deferred_due_date} onSaved={onChanged} />}
            </div>
          );
        })}
      </div>
    );
  }

  return null;
}
