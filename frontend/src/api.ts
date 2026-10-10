import { readAuthToken } from "./auth/tokenStorage";
import { apiBasePath } from "./basePath";

const BASE = apiBasePath();

function authHeaders(): Record<string, string> {
  const token = readAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders(), ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(userFacingApiError(res.status, text));
  }
  return res.json();
}

function userFacingApiError(status: number, raw: string): string {
  // Keep backend details out of the default UI. They remain available in
  // server logs, while users get a useful action without schema/exception
  // terminology leaking into Today, Money, or Moves.
  if (status === 401) return "Please sign in again.";
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "We couldn't find that information.";
  if (status === 409) return "That changed while you were working. Refresh and try again.";
  if (status === 422) return "Please check the highlighted information and try again.";
  if (status >= 500) return "Something went wrong. Try again.";
  const plain = raw.trim();
  if (plain && !/[{}\[\]]/.test(plain) && !/(traceback|exception|sql|schema|qualification|lifecycle|provider|internal)/i.test(plain)) {
    return plain;
  }
  return "Something went wrong. Try again.";
}

export interface AuthUser {
  id: string;
  username: string;
  display_name: string;
}

export interface AuthResponse {
  token: string;
  user: AuthUser;
  onboarding_required: boolean;
  remember_me?: boolean;
}

export const api = {
  register: (username: string, password: string, display_name?: string, remember_me = true) =>
    request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password, display_name: display_name || "", remember_me }),
    }),
  login: (username: string, password: string, remember_me = true) =>
    request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, remember_me }),
    }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  me: () =>
    request<AuthUser & { onboarding_required: boolean }>("/auth/me"),
  aiSettings: () => request<AISettingsPayload>("/settings/ai"),
  askFinancialAssistant: (question: string, history: Array<{ role: "user" | "assistant"; content: string }>) =>
    request<{ answer: string; as_of: string; saved: false }>("/ai/financial-assistant", {
      method: "POST", body: JSON.stringify({ question, history }),
    }),
  saveAiSettings: (body: {
    ai_provider: string;
    ai_base_url?: string;
    ai_api_key?: string;
    ai_model?: string;
    ai_allow_remote_openai?: boolean;
    ai_reasoning_effort?: string;
    assistant_model?: string;
    assistant_reasoning_effort?: string;
  }) =>
    request<AISettingsPayload>("/settings/ai", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  health: () => request<Record<string, unknown>>("/profile/health"),
  dti: () => request<Record<string, unknown>>("/profile/dti"),
  incomeSources: () => request<unknown[]>("/income/sources"),
  createIncome: (body: object) =>
    request("/income/sources", { method: "POST", body: JSON.stringify(body) }),
  updateIncome: (id: string, body: object) =>
    request(`/income/sources/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteIncome: (id: string) => request(`/income/sources/${id}`, { method: "DELETE" }),
  markIncomeReceived: (body: {
    income_source_id: string;
    pay_date: string;
    received_date?: string;
    amount?: number;
  }) =>
    request<{ ok: boolean; kind: string; next_pay_date?: string | null; checking_credited: number }>(
      "/income/mark-received",
      { method: "POST", body: JSON.stringify(body) }
    ),
  bills: () => request<BillWithPayment[]>("/bills"),
  unpaidBills: () => request<{ items: UnpaidBillOccurrence[] }>("/bills/unpaid"),
  markObligationPaid: (body: {
    due_date: string;
    paid_date?: string;
    amount?: number;
    bill_id?: string;
    debt_id?: string;
    mortgage_id?: string;
    travel_expense_id?: string;
    misc_spending_id?: string;
    credit_card_id?: string;
    profile_id?: string;
  }) =>
    request<{ ok: boolean; kind: string; next_due_date?: string | null }>(
      "/obligations/mark-paid",
      { method: "POST", body: JSON.stringify(body) }
    ),
  markBillPaid: (id: string, body: { due_date: string; paid_date?: string; amount?: number }) =>
    request(`/bills/${id}/mark-paid`, { method: "POST", body: JSON.stringify(body) }),
  markBillPaidQuick: (id: string) =>
    request<{ ok: boolean; due_date: string; bill: BillWithPayment }>(
      `/bills/${id}/mark-paid/quick`,
      { method: "POST" }
    ),
  approveBillDeferral: (id: string, body: { original_due_date: string; deferred_due_date: string; payee_approved: boolean }) =>
    request(`/bills/${id}/approved-deferral`, { method: "POST", body: JSON.stringify(body) }),
  approvePaymentDeferral: (
    kind: "bill" | "debt" | "mortgage" | "credit_card",
    id: string,
    body: { original_due_date: string; deferred_due_date: string; payee_approved: boolean },
  ) => {
    const prefix = {
      bill: "/bills", debt: "/debts", mortgage: "/profile/mortgages", credit_card: "/profile/credit-cards",
    }[kind];
    return request(`${prefix}/${id}/approved-deferral`, { method: "POST", body: JSON.stringify(body) });
  },
  unmarkBillPaid: (id: string, body: { due_date: string }) =>
    request(`/bills/${id}/unmark-paid`, { method: "POST", body: JSON.stringify(body) }),
  createBill: (body: object) =>
    request("/bills", { method: "POST", body: JSON.stringify(body) }),
  updateBill: (id: string, body: object) =>
    request(`/bills/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteBill: (id: string) => request(`/bills/${id}`, { method: "DELETE" }),
  moveMiscToBills: (id: string, body: object) =>
    request<{ bill_id: string; archived_misc_id: string }>(`/misc/${id}/move-to-bills`, {
      method: "POST", body: JSON.stringify(body),
    }),
  debts: () => request<DebtWithPayment[]>("/debts"),
  cashflow: () => request<Record<string, any>>("/cashflow"),
  createDebt: (body: object) =>
    request("/debts", { method: "POST", body: JSON.stringify(body) }),
  updateDebt: (id: string, body: object) =>
    request(`/debts/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteDebt: (id: string) => request(`/debts/${id}`, { method: "DELETE" }),
  markDebtPaid: (id: string, body: { due_date: string; paid_date?: string; amount?: number }) =>
    request(`/debts/${id}/mark-paid`, { method: "POST", body: JSON.stringify(body) }),
  markDebtPaidQuick: (id: string) =>
    request<{ ok: boolean; due_date: string; debt: DebtWithPayment }>(
      `/debts/${id}/mark-paid/quick`,
      { method: "POST" }
    ),
  unmarkDebtPaid: (id: string, body: { due_date: string }) =>
    request(`/debts/${id}/unmark-paid`, { method: "POST", body: JSON.stringify(body) }),
  debtStrategy: (s: string) => request(`/debts/strategy/${s}`),
  researchSettings: () =>
    request<{
      fields: { key: string; configured: boolean; has_value?: boolean; preview?: string | null }[];
    }>("/settings/research"),
  saveResearchSettings: (body: Record<string, string>) =>
    request<{
      fields: { key: string; configured: boolean; preview?: string | null }[];
    }>("/settings/research", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  legacyFileCabinetStatus: () => request<LegacyFileCabinetStatus>("/file-cabinet/status"),
  async exportLegacyFileCabinet() {
    const res = await fetch(`${BASE}/file-cabinet/export`, {
      method: "POST",
      headers: authHeaders(),
    });
    if (!res.ok) throw new Error(await res.text());
    return res.blob();
  },
  onboarding: () => request<OnboardingPayload>("/onboarding"),
  onboardingStart: () => request<OnboardingPayload>("/onboarding/start", { method: "POST" }),
  onboardingStep: (stepId: string) =>
    request<OnboardingPayload>(`/onboarding/step/${stepId}`, { method: "POST" }),
  resetHousehold: (confirm: string) =>
    request<{ ok: boolean; error?: string; message?: string }>("/account/reset-household", {
      method: "POST",
      body: JSON.stringify({ confirm }),
    }),
  listBackups: () => request<{ backups: BackupRecord[] }>("/backup/list"),
  createBackup: (include_evidence: boolean) =>
    request<BackupCreateResult>("/backup/create", {
      method: "POST",
      body: JSON.stringify({ include_evidence }),
    }),
  restoreBackup: (backup_id: string, confirm: string) =>
    request<{ ok: boolean; message?: string; pre_restore_backup_id?: string }>("/backup/restore", {
      method: "POST",
      body: JSON.stringify({ backup_id, confirm }),
    }),
  notificationsInbox: (includeDismissed = false, sync = true) =>
    request<NotificationsInboxPayload>(
      `/notifications/inbox?include_dismissed=${includeDismissed}&sync=${sync}`
    ),
  syncNotifications: () =>
    request<{ created: number; refreshed: number; pushed?: number }>("/notifications/sync", {
      method: "POST",
    }),
  pushPublicKey: () => request<{ public_key: string }>("/push/public-key"),
  pushSubscribe: (body: {
    endpoint: string;
    keys: { p256dh: string; auth: string };
    expirationTime?: number | null;
    device_label?: string;
  }) =>
    request<{ id: string; enabled: boolean }>("/push/subscribe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pushUnsubscribe: (endpoint: string) =>
    request<{ ok: boolean }>("/push/unsubscribe", {
      method: "DELETE",
      body: JSON.stringify({ endpoint }),
    }),
  pushTest: () =>
    request<{ sent: number; failed: number }>("/push/test", { method: "POST" }),
  dismissNotification: (id: string, reason: string) =>
    request<NotificationItem>(`/notifications/${id}/dismiss`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  snoozeNotification: (id: string, days: number) =>
    request<NotificationItem>(`/notifications/${id}/snooze`, {
      method: "POST",
      body: JSON.stringify({ days }),
    }),
  exportBackupSummary: (redacted: boolean, include_evidence: boolean) =>
    request<BackupCreateResult & { export_id: string; redacted: boolean }>("/backup/export-summary", {
      method: "POST",
      body: JSON.stringify({ redacted, include_evidence }),
    }),
  async downloadBackupArtifact(
    backupId: string,
    artifact: "zip" | "ledger" | "summary",
    filename: string
  ): Promise<void> {
    const res = await fetch(`${BASE}/backup/${backupId}/download?artifact=${artifact}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },
  onboardingComplete: () =>
    request<OnboardingPayload>("/onboarding/complete", { method: "POST" }),
  onboardingDeclarations: (body: Partial<OnboardingDeclarations>) =>
    request<OnboardingPayload>("/onboarding/declarations", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  listTransactions: () => request<TransactionRow[]>("/spending/transactions"),
  pendingSpendingCount: () => request<{ count: number }>("/spending/pending-checking"),
  createTransaction: (body: TransactionInput) =>
    request<TransactionRow>("/spending/transactions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  recordPlannedSpending: (body: { budget_category_id: string; planned_date: string; transaction_date: string; amount: number }) =>
    request<TransactionRow>("/spending/record-planned", { method: "POST", body: JSON.stringify(body) }),
  importTransactionsCsv: (content: string) =>
    request<{ imported: number; errors: string[] }>("/spending/transactions/import", {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  listBudgetCategories: () => request<BudgetCategoryRow[]>("/spending/budget"),
  createBudgetCategory: (body: { name: string; usual_amount: number; period: "day" | "week" | "month"; planned_weekday?: number }) =>
    request<BudgetCategoryRow>("/spending/budget", { method: "POST", body: JSON.stringify(body) }),
  updateBudgetCategory: (id: string, body: { name: string; usual_amount: number; period: "day" | "week" | "month"; planned_weekday?: number }) =>
    request<BudgetCategoryRow>(`/spending/budget/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteBudgetCategory: (id: string) =>
    request<{ ok: boolean }>(`/spending/budget/${id}`, { method: "DELETE" }),
  humanCapital: () => request<HumanCapitalListPayload>("/human-capital"),
  createHumanCapital: (body: HumanCapitalInput) =>
    request<{ record: HumanCapitalRecord; summary: HumanCapitalSummary }>("/human-capital", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateHumanCapital: (id: string, body: Partial<HumanCapitalInput>) =>
    request<{ record: HumanCapitalRecord; summary: HumanCapitalSummary }>(
      `/human-capital/${id}`,
      { method: "PUT", body: JSON.stringify(body) }
    ),
  deleteHumanCapital: (id: string) =>
    request<{ ok: boolean; summary: HumanCapitalSummary }>(`/human-capital/${id}`, {
      method: "DELETE",
    }),
  riskChecklist: () => request<RiskChecklistPayload>("/risk-checklist"),
  saveRiskChecklist: (body: Partial<RiskChecklistPayload>) =>
    request<RiskChecklistPayload>("/risk-checklist", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  rentVsOwnCalculate: (body: RentVsOwnInput) =>
    request<RentVsOwnResult>("/rent-vs-own/calculate", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  mortgages: () => request<unknown[]>("/profile/mortgages"),
  creditCards: () => request<unknown[]>("/profile/credit-cards"),
  miscSpending: () =>
    request<{
      items: MiscSpendingRow[];
      catalog: { id: string; label: string }[];
    }>("/misc"),
  createMiscSpending: (body: object) =>
    request("/misc", { method: "POST", body: JSON.stringify(body) }),
  updateMiscSpending: (id: string, body: object) =>
    request(`/misc/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteMiscSpending: (id: string) => request(`/misc/${id}`, { method: "DELETE" }),
  markMiscPaid: (id: string, body: { due_date: string; paid_date?: string; amount?: number }) =>
    request(`/misc/${id}/mark-paid`, { method: "POST", body: JSON.stringify(body) }),
  savingsGoals: () =>
    request<{ goals: SavingsGoalRow[]; plan: GoalPlan }>("/goals"),
  createSavingsGoal: (body: object) =>
    request("/goals", { method: "POST", body: JSON.stringify(body) }),
  updateSavingsGoal: (id: string, body: object) =>
    request(`/goals/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  toggleSavingsGoal: (id: string, enabled: boolean) =>
    request(`/goals/${id}/enabled`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  deleteSavingsGoal: (id: string) => request(`/goals/${id}`, { method: "DELETE" }),
  goalPlan: () => request<GoalPlan>("/goals/plan"),
  borrower: () => request<unknown>("/profile/borrower"),
  upsertBorrower: (body: object) =>
    request("/profile/borrower", { method: "POST", body: JSON.stringify(body) }),
  recordCushionContribution: (amount: number) =>
    request("/profile/cushion-contribution", { method: "POST", body: JSON.stringify({ amount }) }),
  liquidAccounts: () => request<unknown[]>("/profile/accounts"),
  createLiquidAccount: (body: object) =>
    request("/profile/accounts", { method: "POST", body: JSON.stringify(body) }),
  updateLiquidAccount: (id: string, body: object) =>
    request(`/profile/accounts/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteLiquidAccount: (id: string) => request(`/profile/accounts/${id}`, { method: "DELETE" }),
  aiHealth: () => request<Record<string, unknown>>("/ai/health"),
  forecast: (days = 90) => request<Forecast>(`/forecast?days=${days}`),
  dataQuality: () => request<QualityReport>("/data-quality"),
  myMoneySummary: () => request<MyMoneySummary>("/my-money/summary"),
  v2Today: () => request<V2TodayPayload>("/v2/today"),
  v2Moves: () => request<V2MovesPayload>("/v2/moves"),
  v2Candidate: (id: string) => request<V2Candidate>(`/v2/candidates/${id}`),
  v2CandidateValidation: (id: string) => request<V2ValidationPayload>(`/v2/candidates/${id}/validation`),
  v2SaveCandidateValidation: (id: string, body: V2ValidationFactInput) =>
    request<V2ValidationPayload>(`/v2/candidates/${id}/validation`, { method: "PUT", body: JSON.stringify(body) }),
  v2RecurringResponse: (id: string, body: { response: "keep" | "cancel" | "switch" | "not_recurring" | "not_sure"; new_recurring_amount?: number; cancellation_fee?: number }) =>
    request<V2Candidate>(`/v2/candidates/${id}/recurring-response`, { method: "POST", body: JSON.stringify(body) }),
  v2AcceptCandidate: (id: string) =>
    request<V2CandidateActionResult>(`/v2/candidates/${id}/accept`, { method: "POST" }),
  v2DismissCandidate: (id: string, reason_code: V2DismissalReason, note = "") =>
    request<V2Candidate>(`/v2/candidates/${id}/dismiss`, {
      method: "POST",
      body: JSON.stringify({ reason_code, note }),
    }),
  v2Outcome: (id: string) => request<V2OutcomePayload>(`/v2/candidates/${id}/outcome`),
  v2RecordOutcome: (id: string, body: V2OutcomeInput) =>
    request<V2OutcomeObservation>(`/v2/candidates/${id}/outcome`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  v2CapabilityProfile: () => request<V2CapabilityPayload>("/v2/capability-profile"),
  v2UpdateCapabilityProfile: (body: Partial<V2CapabilityProfile>) =>
    request<V2CapabilityPayload>("/v2/capability-profile", { method: "PUT", body: JSON.stringify(body) }),
  v2AddSkill: (body: Pick<V2CapabilitySkill, "name"> & Partial<V2CapabilitySkill>) =>
    request<V2CapabilitySkill>("/v2/capability-profile/skills", { method: "POST", body: JSON.stringify(body) }),
  v2AddAsset: (body: Pick<V2CapabilityAsset, "name"> & Partial<V2CapabilityAsset>) =>
    request<V2CapabilityAsset>("/v2/capability-profile/assets", { method: "POST", body: JSON.stringify(body) }),
  v2ResearchStatus: () => request<V2ResearchStatus>("/v2/research/status"),
  v2RefreshResearch: (scope: "all" | "growth" | "financial" | "financial_optimization" = "all") =>
    request<V2ResearchRefreshResult>("/v2/research/refresh", { method: "POST", body: JSON.stringify({ scope: scope === "financial" ? "financial_optimization" : scope }) }),
  household: () => request<HouseholdPayload>("/v2/household"),
  updateHousehold: (body: Partial<HouseholdProfile>) => request<HouseholdProfile>("/v2/household", { method: "PUT", body: JSON.stringify(body) }),
  addHouseholdRisk: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/risks", { method: "POST", body: JSON.stringify(body) }),
  addHouseholdMember: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/members", { method: "POST", body: JSON.stringify(body) }),
  updateHouseholdMember: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/members/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdMember: (id: string) => request<Record<string, unknown>>(`/v2/household/members/${id}`, { method: "DELETE" }),
  addHouseholdIncomeRisk: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/income-risks", { method: "POST", body: JSON.stringify(body) }),
  updateHouseholdIncomeRisk: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/income-risks/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdIncomeRisk: (id: string) => request<Record<string, unknown>>(`/v2/household/income-risks/${id}`, { method: "DELETE" }),
  updateHouseholdRisk: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/risks/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdRisk: (id: string) => request<Record<string, unknown>>(`/v2/household/risks/${id}`, { method: "DELETE" }),
  addHouseholdVehicle: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/vehicles", { method: "POST", body: JSON.stringify(body) }),
  updateHouseholdVehicle: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/vehicles/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdVehicle: (id: string) => request<Record<string, unknown>>(`/v2/household/vehicles/${id}`, { method: "DELETE" }),
  addHouseholdCoverage: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/coverage", { method: "POST", body: JSON.stringify(body) }),
  updateHouseholdCoverage: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/coverage/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdCoverage: (id: string) => request<Record<string, unknown>>(`/v2/household/coverage/${id}`, { method: "DELETE" }),
  addHouseholdLifeEvent: (body: Record<string, unknown>) => request<Record<string, unknown>>("/v2/household/life-events", { method: "POST", body: JSON.stringify(body) }),
  updateHouseholdLifeEvent: (id: string, body: Record<string, unknown>) => request<Record<string, unknown>>(`/v2/household/life-events/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteHouseholdLifeEvent: (id: string) => request<Record<string, unknown>>(`/v2/household/life-events/${id}`, { method: "DELETE" }),
  refreshAdvisory: () => request<AdvisoryPayload>("/v2/advisory/refresh", { method: "POST" }),
  advisory: () => request<AdvisoryPayload>("/v2/advisory"),
  dataFixes: () => request<DataFixQueue>("/data-fixes"),
  validateEntry: (kind: string, data: Record<string, unknown>) =>
    request<{ ok: boolean; errors: string[]; warnings: string[] }>("/validate", {
      method: "POST",
      body: JSON.stringify({ kind, data }),
    }),
  importCsv: (kind: string, content: string) =>
    request<{ created: number; errors: string[]; warnings: string[] }>(`/import/${kind}`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }),
  cashflowPlan: (days = 180) => request<CashflowPlan>(`/cashflow/plan?days=${days}`),
  createMortgage: (body: object) =>
    request("/profile/mortgages", { method: "POST", body: JSON.stringify(body) }),
  updateMortgage: (id: string, body: object) =>
    request(`/profile/mortgages/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteMortgage: (id: string) => request(`/profile/mortgages/${id}`, { method: "DELETE" }),
  markMortgagePaid: (id: string, body: { due_date: string; paid_date?: string; amount?: number }) =>
    request(`/profile/mortgages/${id}/mark-paid`, { method: "POST", body: JSON.stringify(body) }),
  createCreditCard: (body: object) =>
    request("/profile/credit-cards", { method: "POST", body: JSON.stringify(body) }),
  updateCreditCard: (id: string, body: object) =>
    request(`/profile/credit-cards/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteCreditCard: (id: string) => request(`/profile/credit-cards/${id}`, { method: "DELETE" }),
  runForecast: (body: {
    horizon_days?: number;
    starting_cash_override?: number;
    planned_spending_override?: number;
  }) =>
    request<Record<string, unknown>>("/forecast/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  paymentArchive: () => request<{ items: PaymentArchiveItem[] }>("/payment-archive"),
  restorePaymentArchive: (body: Record<string, string>) =>
    request<{ ok: boolean; kind: string; restored_due_date?: string }>(
      "/payment-archive/restore",
      { method: "POST", body: JSON.stringify(body) }
    ),
};

export interface FinancialActionRow {
  id: string;
  title: string;
  action_type: string;
  status: string;
  priority: number;
  due_date: string | null;
  expected_monthly_savings: number;
  expected_total_savings: number;
  actual_monthly_savings: number | null;
  actual_total_savings: number | null;
  notes: string;
  dismiss_reason: string;
  is_urgent: boolean;
  is_blocked: boolean;
  blocked_by: string[];
  recommendation_id: string | null;
  offer_id: string | null;
}

export type V2DismissalReason =
  | "owner_irrelevant"
  | "owner_too_much_work"
  | "owner_timing"
  | "owner_capital"
  | "owner_weak_evidence"
  | "owner_other";

export interface V2MoneyState {
  allowance?: {
    period_start: string; period_end: string; next_reset: string; period_days: number;
    budget: number; spent: number; remaining: number; comfort_holdback: number;
    known: boolean; funding_shortfall: number; total_discretionary_planned: number;
    safe_period_capacity?: number; retained_per_period?: number; planned_retained?: number; retention_percent?: number;
    emergency_current: number | null; emergency_goal: number; monthly_contribution: number;
    projected_emergency: number; contributions: Array<{date: string; amount: number; projected_fund: number}>;
    projection: Array<{date: string; balanceAfterSpending: number; cushion: number | null;
      surprises: number; uncommitted: number | null; extraSpend: number | null}>;
  };
  free_to_use_now?: number;
  planning_horizon_days?: number;
  planning_through?: string;
  payment_plan?: V2MoneyState["payment_plan_30_days"];
  cash_rescue?: {
    first_gap?: { date: string; payment?: string | null; amount: number } | null;
    gap_depends_on_living_holdback: boolean;
    lowest_30_day_balance: number;
    normal_spending_entered: boolean;
    normal_spending_held: number;
    cushion_target: number;
    cushion_configured: boolean;
    cushion_gap: number;
    next_income?: { date: string; amount: number } | null;
    needed_before_income: number;
    conditional_only: boolean;
    scenarios: Array<{
      payment: string;
      original_date: string;
      proposed_date: string;
      amount: number;
      changes: Array<{ payment: string; original_date: string; amount: number }>;
      remaining_gap_before_income: number;
      remaining_gap_30_days: number;
      closes_recorded_gap: boolean;
      requires_payee_confirmation: boolean;
    }>;
  };
  liquid_resources?: number;
  known_obligation_reserve?: number;
  expected_variable_reserve?: number;
  uncertainty_reserve?: number;
  emergency_reserve_target?: number;
  protected_cash?: number;
  flexible_cash?: number;
  deployable_surplus?: number;
  experimental_capital?: number;
  constraining_horizon?: number | string | null;
  minimum_projected_balance?: number | null;
  risk_windows?: Array<Record<string, unknown>>;
  uncertainty_provenance?: Array<Record<string, unknown>>;
  safety_cushion?: number;
  cash_cushion_goal?: number;
  cash_cushion_current?: number | null;
  cash_cushion_gap?: number | null;
  cash_cushion_refill?: number;
  dated_lowest_after_normal_spending?: number;
  working_safety_holdback?: number;
  discretionary_before_refill?: number;
  total_flexible_30_days?: number;
  discretionary_spending_30_days?: number;
  discretionary_share?: number;
  flexible_safety_remaining?: number;
  extra_safety_holdback?: number;
  additional_uncertainty_reserve?: number;
  emergency_reserve_account_balance?: number;
  protected_cash_breakdown?: {
    for_bills?: number;
    for_normal_spending?: number;
    safety_cushion?: number;
    total?: number;
  };
  today_breakdown?: {
    free_to_use_now?: number;
    planning_horizon_days?: number;
    planning_through?: string;
    required_planning?: number;
    normal_spending_planning?: number;
    income_planning?: number;
    available_now?: number;
    required_payments?: number;
    normal_spending?: number;
    safety_cushion?: number;
    cash_cushion_goal?: number;
    cash_cushion_current?: number | null;
    cash_cushion_gap?: number | null;
    cash_cushion_refill?: number;
    dated_lowest_after_normal_spending?: number;
    working_safety_holdback?: number;
    discretionary_before_refill?: number;
    total_flexible_30_days?: number;
    discretionary_spending_30_days?: number;
    discretionary_share?: number;
    flexible_safety_remaining?: number;
    extra_safety_holdback?: number;
    extra_spending?: number;
    next_income?: number;
    next_income_date?: string | null;
    required_before_next_income?: number;
    income_30_days?: number;
    required_30_days?: number;
    lowest_balance_30_days?: number;
    projected_balance_30_days?: number;
    coverage_status?: "covered" | "covered_below_cushion" | "shortfall" | "savings_shortfall" | "unknown";
    coverage_message?: string;
    extra_reason?: string;
    first_shortfall_date?: string | null;
    first_shortfall_name?: string | null;
    missing_due_dates?: Array<Record<string, unknown>>;
    reserve_components?: Record<string, number>;
  };
  payment_plan_30_days?: {
    as_of: string;
    through: string;
    opening_checking: number;
    projected_ending_checking: number;
    lowest_checking: number;
    same_day_income_first: boolean;
    suggestions_excluded_from_balance: boolean;
    rows: Array<{
      date: string;
      kind: "income" | "obligation";
      category: string;
      spending_kind?: "daily" | "groceries" | "recorded_pending";
      suggested_day?: boolean;
      awaiting_checking_update?: boolean;
      pending_checking_amount?: number;
      pending_grocery_amount?: number;
      name: string;
      amount: number;
      original_due_date?: string | null;
      deferred_once: boolean;
      grace_used?: boolean;
      late_fee_date?: string | null;
      projected_balance_after: number;
    }>;
  };
}

export interface V2Candidate {
  id: string;
  title: string;
  summary: string;
  category: string;
  source_type: string;
  source_label: string;
  source_url: string;
  lifecycle_status: string;
  qualification_status: string;
  verification_status: string;
  freshness_status: string;
  safety_eligible: boolean;
  rank_score: number | null;
  expected_net_low: number | null;
  expected_net_high: number | null;
  required_capital: number | null;
  capital_at_risk: number | null;
  setup_hours: number | null;
  recurring_hours_per_week: number | null;
  time_to_value_days: number | null;
  evidence: Array<Record<string, unknown>>;
  rejection_reasons: string[];
  score_components: Record<string, unknown>;
  experiment: Record<string, unknown>;
  quality_state?: string;
  decision_brief?: V2DecisionBrief;
}

export interface V2DecisionBrief {
  quality_state: string;
  why_now: string;
  value_type: "savings" | "income" | "cost_avoidance" | "validation" | string;
  expected_net_low: number | null;
  expected_net_high: number | null;
  probability_low: number | null;
  probability_high: number | null;
  required_capital: number | null;
  capital_at_risk: number | null;
  recurring_operating_cost: number | null;
  liquidity_lockup_days: number | null;
  setup_hours: number | null;
  recurring_hours_per_week: number | null;
  time_to_value_days: number | null;
  value_horizon_days: number | null;
  capability_fit_score: number;
  scalability: number;
  automation_potential: number;
  reversibility: number;
  complexity: number;
  downside_severity: number;
  verification_status: string;
  freshness_status: string;
  assumptions: string[];
  missing_facts: string[];
  next_step: string;
  safety_eligible: boolean;
  rank_score: number | null;
  score_components: Record<string, unknown>;
  experiment: Record<string, unknown>;
}

export interface V2ValidationFactInput {
  fact_type: string;
  value: unknown;
  units?: string;
  source_type?: string;
  source_label?: string;
  observed_at?: string;
  expires_at?: string;
  verification_status?: string;
}

export interface V2ValidationPayload {
  candidate_id: string;
  category: string;
  required_facts: Array<{ fact_type: string; units: string; description: string; required?: boolean }>;
  missing_fact_types: string[];
  fact_states: Record<string, string>;
  facts: Array<Record<string, unknown>>;
  allowed_inputs?: Record<string, Array<{ id: string; label: string }>>;
  next_step: string;
}

export interface V2CandidateAction extends FinancialActionRow {
  opportunity_candidate_id: string | null;
}

export interface V2CandidateActionResult {
  candidate: V2Candidate;
  action: V2CandidateAction;
}

export interface V2QuickStatusItem extends QuickStatusItem {
  kind: string;
}

export interface V2TodayPayload {
  generated_at: string;
  safety_status: "safe" | "tight" | "at_risk";
  financial_state: V2MoneyState;
  cashflow: {
    checking_balance?: number;
    next_pay_date?: string | null;
    expected_income?: number;
    risk_level?: string;
    warnings?: string[];
    horizons?: Record<string, { days?: number; projected_balance?: number; lowest_balance?: number }>;
  };
  immediate_obligations: V2QuickStatusItem[];
  checking_balance_recorded: number | null;
  checking_activity_pending: number;
  spending_attention: {
    grocery_trips: PlannedSpendingAttention[];
    everyday: PlannedSpendingAttention[];
    pending_checking_count: number;
  };
  cushion_step: { amount: number; date: string; recorded: boolean; can_record?: boolean };
  top_moves: V2Candidate[];
  research: V2ResearchStatus;
  active_candidate_actions: V2CandidateAction[];
  resilience?: ResiliencePayload;
  wealth_plan?: WealthPlanPayload;
  top_wealth_action?: { kind: string; title: string; capital: number };
}

export interface PlannedSpendingAttention {
  category_id: string;
  name: string;
  planned_date: string;
  planned_amount: number;
  recorded_amount: number;
  recorded?: boolean;
  awaiting_checking_update?: boolean;
}

export interface V2MovesPayload {
  ranked: V2Candidate[];
  top_moves: V2Candidate[];
  candidates: V2Candidate[];
  history?: V2Candidate[];
  quality?: Record<string, unknown>;
  value?: Record<string, unknown>;
  validation?: Record<string, unknown>;
  advisory?: AdvisoryPayload;
}

export interface ResiliencePayload { monthly_essential_cost: number; monthly_income: number; current_accessible_liquidity: number; current_reserve: number; dedicated_resilience?: number; reserve_gap: number; confidence?: string; missing_domains?: string[]; drivers?: string[]; target: { minimum: number; recommended: number; strong: number; months: Record<string, number> }; scenarios: Array<Record<string, unknown>>; known_risks: Array<Record<string, unknown>>; }
export interface WealthPlanPayload { free_cash: number; allocation: { save: number; grow: number; free: number; free_cash: number; reconciles: boolean }; why: string; automatic_transfer: boolean; d001_unchanged: boolean; }
export interface AdvisoryHypothesis { id?: string; candidate_id?: string; title: string; summary: string; status: string; evidence_needed: string[]; test_capital: number; required_capital: number; capital_at_risk: number; }
export interface AdvisoryPayload { generated_at: string; resilience: ResiliencePayload; wealth_plan: WealthPlanPayload; hypotheses: AdvisoryHypothesis[]; top_wealth_action: { kind: string; title: string; capital: number }; privacy: Record<string, boolean>; }
export interface HouseholdProfile { id: string; risk_tolerance: string; target_reserve_months?: number | null; desired_growth_share?: number | null; desired_free_share?: number | null; dependents_count: number; dedicated_resilience_balance?: number; income_notes: string; planning_notes: string; }
export interface HouseholdPayload { profile: HouseholdProfile; risks: Array<Record<string, unknown>>; life_events: Array<Record<string, unknown>>; coverage: Array<Record<string, unknown>>; vehicles: Array<Record<string, unknown>>; members: Array<Record<string, unknown>>; income_risks: Array<Record<string, unknown>>; }

export interface V2CapabilityProfile {
  id: string;
  max_setup_hours: number | null;
  max_recurring_hours_per_week: number | null;
  preferred_effort_level: string;
  minimum_meaningful_payoff: number | null;
  max_preferred_capital: number | null;
  desired_time_to_value_days: number | null;
  customer_interaction_willingness: boolean | null;
  sales_outreach_willingness: boolean | null;
  physical_labor_willingness: boolean | null;
  prefers_async: boolean;
  prefers_automated: boolean;
  prefers_low_touch: boolean;
  prefers_recurring_income: boolean;
  prefers_scalable_income: boolean;
  allows_one_time_income: boolean;
  [key: string]: unknown;
}

export interface V2CapabilitySkill { id: string; name: string; category: string; proficiency: number; notes: string; }
export interface V2CapabilityAsset { id: string; name: string; asset_type: string; availability: string; notes: string; }
export interface V2CapabilityCompleteness { status: string; defensive: string; growth: string; has_explicit_capability: boolean; message: string; }
export interface V2CapabilityPayload { profile: V2CapabilityProfile; skills: V2CapabilitySkill[]; assets: V2CapabilityAsset[]; refused_activities: string[]; completeness?: V2CapabilityCompleteness; }
export interface V2ResearchStatus { latest_run: Record<string, unknown> | null; last_successful_refresh: Record<string, unknown> | null; next_eligible_refresh: string | null; scheduler_job: string | null; providers: string[]; }
export interface V2ResearchRefreshResult { candidates_created: number; [key: string]: unknown; }
export interface V2OutcomeObservation { id: string; outcome_status: string; actual_net_value: number | null; actual_setup_hours: number | null; [key: string]: unknown; }
export interface V2OutcomePayload { prediction: Record<string, unknown> | null; outcome: V2OutcomeObservation | null; }
export interface V2OutcomeInput {
  financial_action_id?: string;
  outcome_status: "in_progress" | "completed" | "failed" | "abandoned";
  actual_net_value?: number | null;
  actual_setup_hours?: number | null;
  actual_direct_cost?: number | null;
  outcome_reason?: string;
  outcome_notes?: string;
}

export interface ActionPlanPayload {
  urgent: FinancialActionRow[];
  this_week: FinancialActionRow[];
  savings_opportunities: FinancialActionRow[];
  blocked: FinancialActionRow[];
  completed: FinancialActionRow[];
  savings_summary: {
    expected_monthly: number;
    actual_monthly: number;
    expected_total: number;
    actual_total: number;
  };
  all: FinancialActionRow[];
}

export interface LinkedActionSummary {
  id: string;
  title: string;
  status: string;
  action_type?: string;
  due_date?: string | null;
}

export interface Rec {
  id: string;
  title: string;
  action: string;
  priority: number;
  why: string;
  why_matters?: string;
  required_next_step?: string;
  risk_warnings?: string[];
  confidence_note?: string | null;
  risk_level: string;
  status: string;
  category?: string;
  confidence?: number;
  source?: string;
  estimated_monthly_savings?: number;
  estimated_total_savings?: number;
  requires_manual_verification?: boolean;
  offer_backed?: boolean;
  offer_provider?: string | null;
  offer_id?: string | null;
  offer_verdict?: string | null;
  expiration_date?: string | null;
  linked_account_type?: string | null;
  linked_account_id?: string | null;
  linked_account_label?: string | null;
  compare_details?: Record<string, unknown> | null;
  linked_action?: LinkedActionSummary | null;
}

export interface QuickStatusItem {
  name?: string;
  amount?: number;
  due_date?: string;
  kind?: string;
  bill_id?: string | null;
  debt_id?: string | null;
  mortgage_id?: string | null;
  travel_expense_id?: string | null;
  misc_spending_id?: string | null;
  credit_card_id?: string | null;
  profile_id?: string | null;
  income_source_id?: string | null;
  can_mark_paid?: boolean;
  can_mark_received?: boolean;
  overdue?: boolean;
  days_overdue?: number | null;
}

export interface PaymentArchiveItem extends QuickStatusItem {
  archive_id: string;
  action?: "marked_paid" | "marked_received" | "restored" | string;
  status?: "active" | "restored" | string;
  scheduled_date?: string;
  marked_date?: string | null;
  marked_at?: string | null;
  reverses_event_id?: string | null;
  can_restore?: boolean;
}

export interface LiquidityHorizonPayload {
  days: number;
  horizon_key?: number | string;
  income_expected: number;
  obligations_scheduled: number;
  net_scheduled: number;
  projected_balance: number;
  lowest_balance?: number;
  spend_or_save_budget?: number;
  flexible_without_hitting_buffer?: number;
  flexible_today?: number;
  flexible_today_trace?: FlexibleTodayResultPayload;
}

export interface CommandCenterPayload {
  generated_at: string;
  setup_completion: number;
  setup_status: string;
  setup_path: string;
  briefing: {
    overall_status?: string;
    executive_summary?: string;
    cash_status?: Record<string, unknown>;
    path: string;
    briefing_id?: string;
  };
  cash_status: {
    /** 30-day liquidity risk from merged cashflow plan. */
    risk_level?: string;
    safe_to_spend?: number;
    /** Alias of safe_to_spend — 30-day timeline cushion. */
    safe_to_spend_timeline?: number;
    /** Max spend today from forward 180-day obligation waterfall. */
    safe_to_spend_conservative?: number;
    /** Estimated checking balance after 30-day scheduled in/out (before extra spending). */
    projected_cash_after_30_days?: number;
    /** Lowest cushion during 30-day timeline (detail page; not home hero). */
    lowest_30_day_cushion?: number;
    checking_balance?: number;
    headline?: string;
    thirty_day_outlook?: ThirtyDayOutlookPayload;
    flexible_today_detail?: string;
    flexible_today_trace?: FlexibleTodayResultPayload;
    projected_after_30_days_detail?: string;
    thirty_day_plan_summary?: string;
    liquidity_diagnostics?: LiquidityDiagnosticsPayload;
    horizons?: Record<string, LiquidityHorizonPayload>;
    path: string;
  };
  paycheck_window: {
    next_pay_date?: string;
    days_until_pay?: number | null;
    summary: string;
    path: string;
  };
  top_actions: LinkedActionSummary[];
  top_opportunities: { id?: string; title?: string; why?: string; path: string }[];
  top_recommendations: Rec[];
  bills_due_soon: QuickStatusItem[];
  quick_status?: QuickStatusItem[];
  today_priorities?: string[];
  confidence_mode?: string;
  alerts: { severity: string; title: string; message: string; path: string }[];
  forecast_status?: string;
  forecast_path: string;
  financial_health?: FinancialHealthPayload;
}

export interface FlexibleTodayTraceEvent {
  step: number;
  kind: "opening" | "income" | "obligation" | "shortfall" | "other";
  date?: string;
  name?: string;
  amount?: number | null;
  category?: string;
  balance_after?: number | null;
  shortfall?: number;
  detail?: string;
}

export interface FlexibleTodayTracePayload {
  starting_balance: number;
  buffer: number;
  lowest_balance: number;
  ending_balance: number;
  flexible_today: number;
  has_shortfall: boolean;
  horizon_days: number;
  horizon_end: string;
  same_day_order: string;
  past_due_rule: string;
  income_rule: string;
  events: FlexibleTodayTraceEvent[];
  summary: string;
  first_shortfall_date?: string | null;
  first_shortfall_name?: string | null;
}

export interface FlexibleTodayResultPayload {
  flexible_today: number;
  has_shortfall: boolean;
  lowest_balance: number;
  ending_balance: number;
  buffer: number;
  horizon_days: number;
  horizon_end: string;
  method: string;
  formula: string;
  trace: FlexibleTodayTracePayload;
}

export interface CommandCenterRefreshResult {
  snapshot_id?: string;
  recommendation_count: number;
  opportunity_count: number;
  briefing_id?: string;
  command_center: CommandCenterPayload;
}

export interface QualityIssue {
  domain: string;
  check: string;
  message: string;
  severity?: string;
  entity_id?: string;
}

export interface DomainScore {
  domain: string;
  score: number;
  status: string;
  issues: { check: string; message: string; severity: string }[];
  recommended_fixes: string[];
  checks_passed: number;
  checks_failed: number;
}

export interface QualityReport {
  overall_score: number;
  status: string;
  domains: DomainScore[];
  blocking_issues: QualityIssue[];
  warnings: QualityIssue[];
  recommended_fixes: string[];
  checked_at: string;
}

export interface DashboardSection {
  title: string;
  path: string;
  summary: string;
  detail?: string;
}

export interface HomeMetrics {
  cashflow_gauge: number;
  health_score: number;
  dti_percent: number;
  forecast_ok: boolean;
  data_trust_score: number;
  data_trust_usable: boolean;
  opportunities_count: number;
}

export interface UnpaidBillOccurrence {
  kind?: "bill" | "debt";
  bill_id?: string;
  debt_id?: string;
  name: string;
  amount: number;
  due_date: string;
  days_overdue: number;
  recurrence: string;
}

export interface DebtWithPayment {
  id: string;
  creditor: string;
  balance: number;
  apr: number;
  minimum_payment: number;
  due_date?: string | null;
  next_due_date?: string | null;
  recurrence: string;
  debt_type: string;
  unpaid_past_due?: { due_date: string; amount: number }[];
  one_tap?: { due_date: string; amount: number } | null;
}

export interface BillWithPayment {
  id: string;
  name: string;
  amount: number;
  due_date?: string | null;
  next_due_date?: string | null;
  approved_deferral?: { original_due_date: string; deferred_due_date: string; approved_at: string } | null;
  recurrence: string;
  unpaid_past_due?: { due_date: string; amount: number }[];
  one_tap?: { due_date: string; amount: number } | null;
}

export interface PlaybookStepPayload {
  date: string;
  action: string;
  name?: string;
  amount?: number;
  category?: string;
  due_date?: string;
  bill_id?: string | null;
  debt_id?: string | null;
  can_mark_paid?: boolean;
  checking_balance?: number;
  detail?: string;
  balance_after?: number;
}

export interface DashboardPayload {
  home_metrics?: HomeMetrics;
  cashflow: DashboardSection & {
    safe_to_spend?: number;
    safe_to_spend_timeline?: number;
    safe_to_spend_conservative?: number;
    projected_cash_after_30_days?: number;
    lowest_30_day_cushion?: number;
    thirty_day_outlook?: ThirtyDayOutlookPayload;
    flexible_today_detail?: string;
    projected_after_30_days_detail?: string;
    thirty_day_plan_summary?: string;
    safe_to_spend_after_paycheck?: number;
    spendable_after_essentials?: number;
    flexible_before_next_income?: number;
    balance_after_pay_cycle?: number;
    playbook?: PlaybookStepPayload[];
    playbooks?: Record<string, PlaybookStepPayload[]>;
    horizons?: Record<
      string,
      {
        days: number;
        spend_or_save_budget: number;
        projected_balance: number;
        income_expected: number;
        obligations_scheduled: number;
      }
    >;
    liquidity_recommendations?: string[];
    checking_balance?: number;
    committed_until_pay?: number;
    risk_level?: string;
    next_pay_date?: string;
    warnings?: string[];
    liquidity_diagnostics?: LiquidityDiagnosticsPayload;
  };
  cfo_overview: DashboardSection & { score?: number; label?: string; dti?: number };
  forecast: DashboardSection & { status?: string; pressure_score?: number };
  data_quality: DashboardSection & {
    score?: number;
    status?: string;
    blocking_count?: number;
  };
  opportunities: DashboardSection & { count?: number };
  groceries: DashboardSection & { best_store?: string; savings?: number; item_count?: number };
  activity: DashboardSection & {
    events: { event_type: string; created_at: string; severity: string }[];
  };
  setup: { income: boolean; bills: boolean; debts: boolean; checking: boolean };
  plan_headline: string;
  unpaid_past_due?: UnpaidBillOccurrence[];
  financial_health?: FinancialHealthPayload;
}

export interface AIProviderRegistryEntry {
  id: string;
  label: string;
  auth_mode: string;
  adapter: string;
  requires_base_url: boolean;
  requires_api_key: boolean;
  default_base_url?: string | null;
  supports_tools: boolean;
  supports_structured_output: boolean;
  implemented: boolean;
}

export interface MyMoneySection {
  id: string;
  label: string;
  complete: boolean;
  skipped: boolean;
  score: number;
  missing_fields: string[];
  blocking_issues: string[];
  summary: string;
  data_quality_score?: number;
  data_quality_status?: string;
}

export interface MonthlyObligations {
  bills: number;
  debt_minimums: number;
  mortgage_payments: number;
  credit_card_minimums: number;
  travel: number;
  misc?: number;
  total: number;
}

export interface MiscSpendingRow {
  id: string;
  name: string;
  category: string;
  amount: number;
  due_date?: string | null;
  recurrence: string;
  category_label?: string;
}

export interface SavingsGoalRow {
  id: string;
  name: string;
  target_amount: number;
  saved_amount: number;
  due_date?: string | null;
  enabled: boolean;
  linked_account_id?: string | null;
  notes?: string;
}

export interface GoalAnalysis {
  goal_id: string;
  name: string;
  status: string;
  remaining: number;
  progress_pct: number;
  required_monthly?: number | null;
  recommended_contribution?: number;
  on_track?: boolean | null;
  message?: string;
  blocked_reason?: string;
  due_date?: string | null;
  enabled?: boolean;
}

export interface GoalPlan {
  goals: GoalAnalysis[];
  can_contribute: boolean;
  available_surplus: number;
  total_recommended: number;
  enabled_count: number;
  summary: string;
  cashflow_risk_level?: string;
}

export interface MyMoneySummary {
  sections: MyMoneySection[];
  counts: Record<string, number>;
  data_quality: { overall_score: number; status: string };
  monthly_obligations?: MonthlyObligations;
}

export interface DataFixItem {
  id: string;
  check: string;
  category: string;
  severity: string;
  domain: string;
  message: string;
  entity_id?: string;
  fix_path: string;
  fix_hint: string;
}

export interface DataFixQueue {
  count: number;
  blocking_count: number;
  warning_count: number;
  items: DataFixItem[];
  overall_score?: number;
  status?: string;
}

export interface OnboardingDeclarations {
  no_mortgage: boolean;
  no_credit_cards: boolean;
  no_debts: boolean;
  no_bills: boolean;
  skip_grocery: boolean;
  research_acknowledged: boolean;
}

export interface OnboardingSection {
  id: string;
  label: string;
  complete: boolean;
  skipped: boolean;
  score: number;
  missing_fields: string[];
  blocking_issues: string[];
}

export interface OnboardingPayload {
  completion_score: number;
  status: "not_started" | "incomplete" | "usable" | "trusted";
  confidence_mode?: "setup" | "limited" | "full";
  good_enough_ready?: boolean;
  finished_with_limited_confidence?: boolean;
  setup_roadmap?: SetupRoadmapPhase[];
  started_at?: string | null;
  completed_at?: string | null;
  wizard_complete?: boolean;
  error?: string;
  missing_required_fields: string[];
  blocking_issues: string[];
  next_best_step: string;
  data_quality_after_onboarding: {
    overall_score: number;
    status: string;
  };
  sections: OnboardingSection[];
  declarations: OnboardingDeclarations;
  report_readiness: {
    deterministic: boolean;
    ai_enhanced: boolean;
    research_rates: boolean;
    credit_optimization: boolean;
    mortgage_optimization: boolean;
  };
  optimization_readiness: {
    credit: { ready: boolean; skipped: boolean; blocking: string[] };
    mortgage: { ready: boolean; skipped: boolean; blocking: string[] };
  };
}

export interface SetupRoadmapPhase {
  day: number;
  title: string;
  hint: string;
  complete: boolean;
  available: boolean;
  section_ids: string[];
}

export interface TransactionRow {
  id: string;
  amount: number;
  category: string;
  description: string;
  transaction_date: string;
  created_at?: string;
}

export interface TransactionInput {
  amount: number;
  category: string;
  description?: string;
  transaction_date: string;
}

export interface BudgetCategoryRow {
  id: string;
  name: string;
  monthly_limit: number;
  usual_amount?: number | null;
  period?: "day" | "week" | "month";
  planned_weekday?: number;
  spent_this_period: number;
}

export interface AISettingsPayload {
  provider: string;
  provider_label: string;
  adapter: string;
  auth_mode: string;
  base_url: string;
  model: string;
  reasoning_effort: string;
  assistant_model: string;
  assistant_reasoning_effort: string;
  codex_models: string[];
  codex_reasoning_efforts: string[];
  api_key_configured: boolean;
  api_key_preview: string | null;
  remote_allowed: boolean;
  status: "configured" | "misconfigured" | "unavailable";
  warnings: string[];
  health_check: Record<string, unknown>;
  registry: AIProviderRegistryEntry[];
}

export interface CashflowPlanDay {
  date: string;
  income: number;
  bills: number;
  debt_minimums: number;
  mortgage_payments?: number;
  outflow: number;
  net: number;
  balance: number;
}

export interface CashflowPlan {
  horizon_days: number;
  advice: string[];
  milestones: { day: number; date: string; projected_balance: number }[];
  upcoming_events: { date: string; type: string; name: string; amount: number }[];
  daily_series: CashflowPlanDay[];
  lowest_balance: { date: string; balance: number };
  projected_balance_end: number;
  monthly_bills_estimate?: number;
  monthly_debt_minimums?: number;
  monthly_mortgage_payments?: number;
  monthly_obligations?: MonthlyObligations;
  starting_cash?: number;
}

export interface Forecast {
  forecast_id: string;
  snapshot_id: string;
  horizon_days: number;
  starting_cash: number;
  ending_cash: number;
  lowest_cash_day: { date: string; cash_balance: number };
  first_shortfall_date: string | null;
  first_buffer_breach_date: string | null;
  pressure_score: number;
  status: string;
  timeline: Array<{
    date: string;
    starting_balance: number;
    income: number;
    bills: number;
    debt_minimums: number;
    planned_spending: number;
    ending_balance: number;
    events: string[];
  }>;
  risk_windows: Array<{
    type: string;
    start: string;
    end: string;
    severity: string;
    message: string;
  }>;
  recommendations: string[];
}

export interface BackupRecord {
  backup_id: string;
  created_at: string;
  include_evidence: boolean;
  size_bytes: number;
  pre_restore?: boolean;
}

export interface BackupCreateResult {
  backup_id: string;
  created_at: string;
  include_evidence: boolean;
  size_bytes: number;
  download_path: string;
}

export interface NotificationItem {
  id: string;
  notification_type: string;
  severity: string;
  title: string;
  body: string;
  link_path: string;
  status: string;
  snoozed_until?: string | null;
  dismiss_reason?: string;
}

export interface NotificationsInboxPayload {
  critical: NotificationItem[];
  due_this_week: NotificationItem[];
  active: NotificationItem[];
  snoozed: NotificationItem[];
  dismissed: NotificationItem[];
  counts: { critical: number; active: number; snoozed: number; dismissed: number };
}

export interface DailyBriefingRisk {
  severity?: string;
  title?: string;
  detail?: string;
  category?: string;
}

export interface OpportunityItem {
  opportunity_key?: string;
  category?: string;
  title?: string;
  why?: string;
  user_status?: string;
  from_web_today?: boolean;
  source_url?: string;
  scores?: { overall_priority_score?: number; financial_impact_score?: number };
  [key: string]: unknown;
}

export interface CfoReportSummary {
  id: string;
  generated_at: string | null;
  cash_status: string;
  body?: { context?: Record<string, unknown>; ai?: Record<string, unknown> };
}

export interface LegacyFileCabinetStatus {
  retired: boolean;
  archive_version: number;
  has_data: boolean;
  summary: {
    folder_count: number;
    document_count: number;
    note_count: number;
    file_count: number;
    verified_file_count: number;
    missing_file_count: number;
    unsafe_path_count: number;
    orphan_file_count: number;
  };
}

export interface DailyBriefingOpportunity {
  title?: string;
  why?: string;
  estimated_monthly_savings?: number;
  category?: string;
}

export interface DailyBriefingAction {
  title?: string;
  due_date?: string;
  status?: string;
}

export interface LiquiditySkippedEventPayload {
  type: string;
  label: string;
  missing_field: string;
  reason: string;
  ref_id?: string | null;
}

export interface LiquidityDiagnosticsPayload {
  skipped_undated_events?: LiquiditySkippedEventPayload[];
  warning_count?: number;
}

export interface ThirtyDayOutlookPayload {
  status?: "ok" | "tight" | "shortfall";
  summary?: string;
  safe_to_spend?: number;
  scheduled_income?: number;
  scheduled_obligations?: number;
  net_scheduled?: number;
  projected_balance?: number;
  lowest_balance?: number;
  first_shortfall_date?: string | null;
  first_shortfall_name?: string | null;
}

export interface CfoPaymentPlanPayload {
  horizon_days?: number;
  status?: "ok" | "tight" | "danger" | "shortfall";
  starting_cash?: number;
  safe_to_spend?: number;
  thirty_day_outlook?: ThirtyDayOutlookPayload;
  buffer_target?: number;
  next_pay_date?: string | null;
  following_pay_date?: string | null;
  strategy_summary?: string;
  recommendations?: string[];
  timeline?: Array<{
    date?: string;
    action?: string;
    name?: string;
    amount?: number;
    category?: string;
    detail?: string;
    balance_after?: number;
    checking_balance?: number;
    shortfall?: number;
    due_date?: string;
    income_source_id?: string | null;
    can_mark_received?: boolean;
    bill_id?: string | null;
    debt_id?: string | null;
    mortgage_id?: string | null;
    travel_expense_id?: string | null;
    misc_spending_id?: string | null;
    credit_card_id?: string | null;
    profile_id?: string | null;
    can_mark_paid?: boolean;
    step_kind?: "in" | "out" | "alert";
  }>;
  month_timeline?: CfoPaymentPlanPayload["timeline"];
  phases?: Array<{
    phase?: string;
    title?: string;
    items?: Array<{
      date?: string;
      name?: string;
      amount?: number;
      category?: string;
      balance_after?: number;
      due_date?: string;
      bill_id?: string | null;
      debt_id?: string | null;
      can_mark_paid?: boolean;
    }>;
  }>;
  payment_count?: number;
  payments_this_week?: number;
  payments_later?: number;
  total_scheduled?: number;
  shortfalls?: Array<Record<string, unknown>>;
}

export interface DailyBriefingExpiringItem {
  offer_id?: string;
  offer_name?: string;
  expiration_date?: string;
  days_left?: number;
}

export interface FinancialHealthPayload {
  foundation_score: number;
  cash_runway_status: string;
  savings_rate_status: string;
  debt_pressure_status: string;
  goal_alignment_status: string;
  insurance_status: string;
  tax_planning_status: string;
  estate_planning_status: string;
  human_capital_status: string;
  notes: string[];
}

export interface LifeGoalsPayload {
  positive_emotion: string[];
  engagement: string[];
  relationships: string[];
  meaning: string[];
  accomplishment: string[];
}

export interface SpendingAlignmentPayload {
  aligned_spending: number;
  neutral_spending: number;
  misaligned_spending: number;
  alignment_percent: number;
  top_misaligned_categories: Array<{ category: string; amount: number }>;
  notes: string[];
}

export interface HumanCapitalRecord {
  id: string;
  date: string;
  type: string;
  description: string;
  cost: number;
  expected_return: string;
  actual_income_impact: number;
  notes: string;
  updated_at?: string;
}

export interface HumanCapitalInput {
  date?: string;
  type: string;
  description?: string;
  cost?: number;
  expected_return?: string;
  actual_income_impact?: number;
  notes?: string;
}

export interface HumanCapitalSummary {
  record_count: number;
  investments_this_year: number;
  total_invested_this_year: number;
  actual_income_impact_this_year: number | null;
  status: string;
}

export interface HumanCapitalListPayload {
  records: HumanCapitalRecord[];
  summary: HumanCapitalSummary;
}

export type RiskChecklistValue =
  | "complete"
  | "partial"
  | "missing"
  | "unknown"
  | "not_applicable";

export interface RiskChecklistPayload {
  emergency_fund: RiskChecklistValue;
  life_insurance: RiskChecklistValue;
  disability_insurance: RiskChecklistValue;
  will: RiskChecklistValue;
  beneficiaries_reviewed: RiskChecklistValue;
  estate_plan: RiskChecklistValue;
  last_reviewed: string | null;
}

export interface RentVsOwnInput {
  home_price?: number;
  monthly_rent?: number;
  down_payment?: number;
  mortgage_rate?: number;
  property_tax_rate?: number;
  home_insurance_monthly?: number;
  maintenance_rate?: number;
  expected_investment_return?: number;
  years?: number;
}

export interface RentVsOwnResult {
  monthly_ownership_unrecoverable_cost: number;
  monthly_rent_cost: number;
  five_percent_rule_rent_equivalent: number;
  renting_advantage_monthly: number;
  buying_advantage_monthly: number;
  notes: string[];
}

export interface DailyBriefingPayload {
  briefing_id: string;
  generated_at: string;
  overall_status: "stable" | "watch" | "stressed" | "danger";
  cash_status: Record<string, unknown>;
  top_risks: DailyBriefingRisk[];
  top_opportunities: DailyBriefingOpportunity[];
  urgent_actions: DailyBriefingAction[];
  forecast_summary: Record<string, unknown>;
  grocery_summary: Record<string, unknown>;
  memory_summary: Record<string, unknown>;
  expiring_items: DailyBriefingExpiringItem[];
  recommendation_changes: Array<Record<string, unknown>>;
  confidence: number;
  sections?: {
    next_bills?: Array<{ name?: string; amount?: number; due_date?: string; kind?: string }>;
    payment_plan?: CfoPaymentPlanPayload;
    changes_since_last?: Array<Record<string, unknown>>;
    actions_this_week?: DailyBriefingAction[];
    financial_wins?: Array<{ title?: string; actual_monthly_savings?: number | null }>;
  };
  ai_narrative?: {
    executive_summary?: string;
    next_30_days_briefing?: string;
    priorities_today?: string[];
    source?: string;
  } | null;
}
