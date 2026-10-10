export const LEGACY_TAB_TO_SECTION: Record<string, string> = {
  paycheck: "income",
  accounts: "cash_on_hand",
  bills: "bills",
  debts: "debts",
};

export const MONEY_SECTION_IDS = [
  "overview",
  "cash_on_hand",
  "income",
  "bills",
  "debts",
  "mortgage",
  "credit_cards",
  "misc",
  "goals",
] as const;

export type MoneySectionId = (typeof MONEY_SECTION_IDS)[number];

export function resolveMoneySection(params: URLSearchParams): MoneySectionId | null {
  const section = params.get("section");
  if (section && MONEY_SECTION_IDS.includes(section as MoneySectionId)) {
    return section as MoneySectionId;
  }
  const tab = params.get("tab");
  if (tab && LEGACY_TAB_TO_SECTION[tab]) {
    return LEGACY_TAB_TO_SECTION[tab] as MoneySectionId;
  }
  return null;
}

export function moneySectionHref(sectionId: string): string {
  return `/money?section=${encodeURIComponent(sectionId)}`;
}

export function moneySectionLabel(id: MoneySectionId): string {
  if (id === "overview") return "Overview";
  if (id === "cash_on_hand") return "Money you have";
  if (id === "credit_cards") return "Credit cards";
  if (id === "mortgage") return "Housing";
  if (id === "misc") return "Everyday spending";
  if (id === "goals") return "Goals";
  return id.charAt(0).toUpperCase() + id.slice(1).replace("_", " ");
}
