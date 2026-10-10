/** Helpers for setup wizard inline forms (save on Next). */

export const WIZARD_FORM_ID = "wizard-step-form";

export type WizardInlineKind =
  | "paycheck"
  | "cash_profile"
  | "bill"
  | "debt"
  | "housing"
  | "credit_card"
  | "ai";

function isBlank(value: FormDataEntryValue | null): boolean {
  return value == null || String(value).trim() === "";
}

export function isWizardFormEmpty(
  kind: WizardInlineKind,
  fd: FormData,
  formEl?: HTMLFormElement | null
): boolean {
  switch (kind) {
    case "paycheck":
      return (
        isBlank(fd.get("name")) &&
        isBlank(fd.get("amount")) &&
        isBlank(fd.get("next_pay"))
      );
    case "cash_profile":
      return false;
    case "bill":
      return isBlank(fd.get("name")) && isBlank(fd.get("amount"));
    case "debt":
      return (
        isBlank(fd.get("creditor")) &&
        isBlank(fd.get("balance")) &&
        isBlank(fd.get("apr")) &&
        isBlank(fd.get("min"))
      );
    case "credit_card":
      return (
        isBlank(fd.get("issuer")) &&
        isBlank(fd.get("balance")) &&
        isBlank(fd.get("limit")) &&
        isBlank(fd.get("apr")) &&
        isBlank(fd.get("min"))
      );
    case "housing": {
      const mode = (formEl?.dataset.housingMode || "rent") as "rent" | "mortgage";
      if (mode === "rent") {
        return isBlank(fd.get("monthly_rent")) && isBlank(fd.get("rent_due_date"));
      }
      return (
        isBlank(fd.get("lender")) &&
        isBlank(fd.get("balance")) &&
        isBlank(fd.get("rate")) &&
        isBlank(fd.get("payment")) &&
        isBlank(fd.get("value"))
      );
    }
    case "ai":
      return false;
    default:
      return true;
  }
}

export function isOptionalWizardStep(stepId: string): boolean {
  // ponytail: housing/debts/cards/AI can be finished later from Household setup once core data unlocks the dashboard.
  return stepId === "debts" || stepId === "mortgage" || stepId === "credit_cards" || stepId === "ai";
}
