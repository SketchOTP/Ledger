import { type ReactNode, useMemo } from "react";
import { Card, IconPencil, ListRow } from "../ui";
import { sortBillsByDueDate } from "../../lib/billSort";
import { moneySectionHref, moneySectionLabel, type MoneySectionId } from "../../lib/moneyNav";
import { recurrenceLabel, type MiscRow } from "./RecordEditors";

type Borrower = {
  checking_balance?: number;
  emergency_fund_balance?: number;
  housing_status?: string;
  monthly_rent?: number;
};

type LiquidAccount = {
  id: string;
  account_name: string;
  account_type: string;
  institution?: string;
  balance: number;
};

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
  recurrence: string;
};

type DebtRow = {
  id: string;
  creditor: string;
  balance: number;
  minimum_payment: number;
  apr: number;
  recurrence?: string;
};

function money(n: number | undefined | null): string {
  return `$${Number(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function paymentBalanceTrail(payment: number, balance: number): string {
  return `${money(payment)}/mo · balance ${money(balance)}`;
}

function OverviewSection({
  title,
  sectionId,
  onEditSection,
  children,
  empty,
  hasContent = true,
}: {
  title: string;
  sectionId: MoneySectionId;
  onEditSection: (id: MoneySectionId) => void;
  children?: ReactNode;
  empty?: string;
  hasContent?: boolean;
}) {
  if (!hasContent) return null;
  return (
    <section className="money-overview-section" aria-labelledby={`overview-${sectionId}`}>
      <div className="money-overview-head">
        <h4 id={`overview-${sectionId}`}>{title}</h4>
        <button
          type="button"
          className="circle-action-btn"
          aria-label={`Edit ${title}`}
          title={`Open ${moneySectionLabel(sectionId)} tab`}
          onClick={() => onEditSection(sectionId)}
        >
          <IconPencil size={15} />
        </button>
      </div>
      {children}
      {empty && !children ? <p className="money-overview-empty">{empty}</p> : null}
    </section>
  );
}

export function MoneyOverview({
  borrower,
  accounts,
  income,
  bills,
  debts,
  mortgages,
  cards,
  miscItems,
  onEditSection,
}: {
  borrower: Borrower | null;
  accounts: LiquidAccount[];
  income: IncomeRow[];
  bills: BillRow[];
  debts: DebtRow[];
  mortgages: { account: Record<string, unknown> }[];
  cards: { card: Record<string, unknown> }[];
  miscItems: MiscRow[];
  onEditSection: (id: MoneySectionId) => void;
}) {
  const isRent = (borrower?.housing_status || "rent").toLowerCase() === "rent";
  const hasProfileCash =
    borrower &&
    (Number(borrower.checking_balance ?? 0) > 0 || Number(borrower.emergency_fund_balance ?? 0) > 0);
  const billsByDueDate = useMemo(() => sortBillsByDueDate(bills), [bills]);

  return (
    <Card className="money-overview-card">
      <p className="field-hint" style={{ marginTop: 0 }}>
        Everything you&apos;ve entered — tap ✎ on a section to open that tab and edit.
      </p>

      <OverviewSection
        title="Money you have"
        sectionId="cash_on_hand"
        onEditSection={onEditSection}
        hasContent={Boolean(accounts.length || hasProfileCash)}
        empty={!accounts.length && !hasProfileCash ? "No balances entered" : undefined}
      >
        <div className="money-record-list money-overview-list">
          {hasProfileCash && (
            <>
              <ListRow
                title="Checking (profile)"
                trailing={money(borrower?.checking_balance)}
                trailTone="safe"
              />
              <ListRow
                title="Savings / emergency (profile)"
                trailing={money(borrower?.emergency_fund_balance)}
                trailTone="safe"
              />
            </>
          )}
          {accounts.map((a) => (
            <ListRow
              key={a.id}
              title={`${a.account_name} (${a.account_type})`}
              subtitle={a.institution || undefined}
              trailing={money(a.balance)}
              trailTone="safe"
            />
          ))}
        </div>
      </OverviewSection>

      <OverviewSection
        title="Income"
        sectionId="income"
        onEditSection={onEditSection}
        hasContent={Boolean(income.length)}
        empty={!income.length ? "No paychecks" : undefined}
      >
        {income.length > 0 && (
          <div className="money-record-list money-overview-list">
            {income.map((row) => (
              <ListRow
                key={row.id}
                title={row.source_name}
                subtitle={`${row.pay_frequency}${row.next_pay_date ? ` · next ${String(row.next_pay_date).slice(0, 10)}` : ""}`}
                trailing={money(row.amount)}
                trailTone="safe"
              />
            ))}
          </div>
        )}
      </OverviewSection>

      <OverviewSection
        title="Bills"
        sectionId="bills"
        onEditSection={onEditSection}
        hasContent={Boolean(billsByDueDate.length)}
        empty={!billsByDueDate.length ? "No bills" : undefined}
      >
        {billsByDueDate.length > 0 && (
          <div className="money-record-list money-overview-list">
            {billsByDueDate.map((row) => (
              <ListRow
                key={row.id}
                title={row.name}
                subtitle={`${row.recurrence}${row.due_date ? ` · due ${String(row.due_date).slice(0, 10)}` : ""}`}
                trailing={money(row.amount)}
                trailTone="danger"
              />
            ))}
          </div>
        )}
      </OverviewSection>

      <OverviewSection
        title="Debts"
        sectionId="debts"
        onEditSection={onEditSection}
        hasContent={Boolean(debts.length)}
        empty={!debts.length ? "No debts listed" : undefined}
      >
        {debts.length > 0 && (
          <div className="money-record-list money-overview-list">
            {debts.map((row) => (
              <ListRow
                key={row.id}
                title={row.creditor}
                subtitle={`${recurrenceLabel(row.recurrence)} · ${row.apr}% interest rate`}
                trailing={paymentBalanceTrail(row.minimum_payment, row.balance)}
                trailTone="danger"
              />
            ))}
          </div>
        )}
      </OverviewSection>

      <OverviewSection
        title="Housing"
        sectionId="mortgage"
        onEditSection={onEditSection}
        hasContent={Boolean(mortgages.length || (isRent && Number(borrower?.monthly_rent ?? 0) > 0))}
        empty={!mortgages.length && !(isRent && Number(borrower?.monthly_rent ?? 0) > 0) ? "Not set" : undefined}
      >
        <div className="money-record-list money-overview-list">
          {isRent && Number(borrower?.monthly_rent ?? 0) > 0 && (
            <ListRow
              title="Rent"
              trailing={`${money(borrower?.monthly_rent)}/mo`}
              trailTone="danger"
            />
          )}
          {mortgages.map((m) => {
            const a = m.account as Record<string, unknown>;
            const pmt = Number(a.monthly_payment ?? 0) + Number(a.escrow_amount ?? 0);
            return (
              <ListRow
                key={String(a.id)}
                title={String(a.lender)}
                subtitle={`${recurrenceLabel(String(a.recurrence || "monthly"))} · ${Number(a.interest_rate)}%`}
                trailing={paymentBalanceTrail(pmt, Number(a.current_balance))}
                trailTone="danger"
              />
            );
          })}
        </div>
      </OverviewSection>

      <OverviewSection
        title="Credit cards"
        sectionId="credit_cards"
        onEditSection={onEditSection}
        hasContent={Boolean(cards.length)}
        empty={!cards.length ? "No credit cards" : undefined}
      >
        {cards.length > 0 && (
          <div className="money-record-list money-overview-list">
            {cards.map((item, i) => {
              const c = item.card as Record<string, unknown>;
              const id = String(c.id ?? i);
              return (
                <ListRow
                  key={id}
                  title={`${c.issuer} — ${c.card_name ?? c.issuer}`}
                  subtitle={`${recurrenceLabel(String(c.recurrence || "monthly"))} · ${Number(c.apr)}% APR`}
                  trailing={paymentBalanceTrail(
                    Number(c.minimum_payment),
                    Number(c.balance)
                  )}
                  trailTone="danger"
                />
              );
            })}
          </div>
        )}
      </OverviewSection>

      <OverviewSection
        title="Miscellaneous"
        sectionId="misc"
        onEditSection={onEditSection}
        hasContent={Boolean(miscItems.length)}
        empty={!miscItems.length ? "No discretionary spending" : undefined}
      >
        {miscItems.length > 0 && (
          <div className="money-record-list money-overview-list">
            {miscItems.map((row) => (
              <ListRow
                key={row.id}
                title={row.name}
                subtitle={`${row.category_label || row.category} · ${recurrenceLabel(row.recurrence)}`}
                trailing={money(row.amount)}
                trailTone="danger"
              />
            ))}
          </div>
        )}
      </OverviewSection>
    </Card>
  );
}
