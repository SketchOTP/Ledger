import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { AuthScreen } from "../components/AuthScreen";
import { QuickAddForm } from "../components/entry/QuickAddForms";
import { CashProfileForm } from "../components/money/CashProfileForm";
import { HousingForm } from "../components/money/HousingForm";
import { AiSetupForm } from "../components/money/AiSetupForm";
import { WizardNav } from "../components/WizardNav";
import { Card, Toggle } from "../components/ui";
import { api, type OnboardingDeclarations, type OnboardingPayload, type OnboardingSection } from "../api";
import {
  isOptionalWizardStep,
  isWizardFormEmpty,
  WIZARD_FORM_ID,
} from "../lib/wizardFormUtils";

type InlineFormKind =
  | "paycheck"
  | "cash_profile"
  | "bill"
  | "debt"
  | "housing"
  | "credit_card"
  | "ai";

type WizardStep = {
  id: string;
  title: string;
  explain: string;
  sectionIds?: string[];
  declarationKey?: keyof OnboardingDeclarations;
  declarationLabel?: string;
  inlineForm?: InlineFormKind;
  moneyLink?: { to: string; label: string };
};

const STEPS: WizardStep[] = [
  {
    id: "welcome",
    title: "Welcome",
    explain: "Let's set up your household finances in a few quick steps.",
  },
  {
    id: "income",
    title: "Income & paycheck",
    explain: "Enter your take-home pay, how often you're paid, and your next pay date.",
    sectionIds: ["income"],
    inlineForm: "paycheck",
  },
  {
    id: "cash",
    title: "Cash, buffer & credit",
    explain: "Checking, savings, emergency buffer goal, and credit score (score is optional).",
    sectionIds: ["cash_on_hand", "target_buffer", "credit_score"],
    inlineForm: "cash_profile",
  },
  {
    id: "bills",
    title: "Bills",
    explain: "Add each bill with name, amount, and due date. Turn on Repeats for monthly bills.",
    sectionIds: ["bills"],
    inlineForm: "bill",
  },
  {
    id: "debts",
    title: "Debts",
    explain:
      "Add loans or other debts if you have any. Leave blank and press → if you're debt-free — entries sync to My Money → Debts.",
    sectionIds: ["debts"],
    inlineForm: "debt",
  },
  {
    id: "mortgage",
    title: "Housing",
    explain: "Choose rent or mortgage and enter the details — they sync to the Housing tab in My Money.",
    sectionIds: ["mortgage"],
    inlineForm: "housing",
  },
  {
    id: "credit_cards",
    title: "Credit cards",
    explain:
      "Add each card with issuer, balance, limit, APR, minimum payment, and due date. Leave blank and press → if you don't use cards — entries sync to My Money → Cards.",
    sectionIds: ["credit_cards"],
    inlineForm: "credit_card",
  },
  {
    id: "ai",
    title: "AI assistant (optional)",
    explain:
      "Optional — connect AI for richer summaries. Skip this step anytime; deterministic advice works without AI.",
    sectionIds: ["ai_provider_check"],
    inlineForm: "ai",
  },
  {
    id: "finish",
    title: "Start using Ledger",
    explain: "When cash, income, and bills are in, you can start — finish the rest anytime from My Money.",
  },
];

function sectionsFor(data: OnboardingPayload, ids: string[] | undefined): OnboardingSection[] {
  if (!ids?.length) return [];
  return ids.map((id) => data.sections.find((s) => s.id === id)).filter(Boolean) as OnboardingSection[];
}

export default function Onboarding() {
  const nav = useNavigate();
  const { refreshMe } = useAuth();
  const [data, setData] = useState<OnboardingPayload | null>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [err, setErr] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const pendingAdvance = useRef(false);

  const load = useCallback(async () => {
    const o = await api.onboarding();
    setData(o);
    if (o.completed_at && o.good_enough_ready !== false) {
      await refreshMe();
      nav("/", { replace: true });
    }
    return o;
  }, [nav, refreshMe]);

  const refreshProgress = useCallback(async () => {
    setErr("");
    try {
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not refresh progress");
    }
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const o = await load();
        if (cancelled) return;
        if (!o.started_at) {
          const started = await api.onboardingStart();
          if (!cancelled) setData(started);
        }
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Could not load setup");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const step = STEPS[stepIndex];
  const secs = step.sectionIds && data ? sectionsFor(data, step.sectionIds) : [];
  const requiredSecs = secs.filter((s) => s.id !== "credit_score");
  const stepDone =
    step.id === "welcome" ||
    step.id === "finish" ||
    requiredSecs.length === 0 ||
    requiredSecs.every((s) => s.complete || s.skipped);

  const saveDeclaration = async (key: keyof OnboardingDeclarations, value: boolean) => {
    if (!data) return;
    setSaving(true);
    try {
      const o = await api.onboardingDeclarations({ [key]: value });
      setData(o);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const finishWizard = async () => {
    setSaving(true);
    setErr("");
    try {
      const o = await api.onboardingComplete();
      setData(o);
      if (!o.wizard_complete && o.error) {
        setErr(o.error);
        return;
      }
      await refreshMe();
      nav("/", { replace: true });
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not finish setup");
    } finally {
      setSaving(false);
    }
  };

  const advanceStep = useCallback(async () => {
    setStepIndex((i) => Math.min(i + 1, STEPS.length - 1));
    await load();
  }, [load]);

  const onWizardSaved = useCallback(async () => {
    await refreshProgress();
    if (pendingAdvance.current) {
      pendingAdvance.current = false;
      await advanceStep();
    }
    setSaving(false);
  }, [advanceStep, refreshProgress]);

  const onWizardSaveFailed = useCallback(() => {
    pendingAdvance.current = false;
    setSaving(false);
  }, []);

  const wizardFormProps = {
    wizardMode: true as const,
    formId: WIZARD_FORM_ID,
    onSaved: onWizardSaved,
    onSaveFailed: onWizardSaveFailed,
  };

  const next = async () => {
    if (step.id === "finish") {
      await finishWizard();
      return;
    }

    setSaving(true);
    setErr("");

    const formEl = document.getElementById(WIZARD_FORM_ID) as HTMLFormElement | null;

    if (step.inlineForm && formEl) {
      const fd = new FormData(formEl);
      const empty = isWizardFormEmpty(step.inlineForm, fd, formEl);

      if (empty && isOptionalWizardStep(step.id)) {
        await advanceStep();
        setSaving(false);
        return;
      }

      if (empty && stepDone) {
        await advanceStep();
        setSaving(false);
        return;
      }

      if (empty && !stepDone) {
        setErr("Enter the required fields, then press → to save and continue.");
        setSaving(false);
        return;
      }

      pendingAdvance.current = true;
      formEl.requestSubmit();
      return;
    }

    if (!stepDone) {
      setErr("Complete the required parts before continuing.");
      setSaving(false);
      return;
    }

    await advanceStep();
    setSaving(false);
  };

  const back = () => {
    pendingAdvance.current = false;
    setErr("");
    setStepIndex((i) => Math.max(0, i - 1));
  };

  if (loading && !data && !err) {
    return (
      <div className="auth-screen">
        <p className="subtitle" style={{ textAlign: "center" }}>
          Loading setup…
        </p>
      </div>
    );
  }

  const pct = data ? Math.round(data.completion_score) : 0;

  return (
    <AuthScreen wide title="Setup">
      {err && <p className="error">{err}</p>}

      <div
        className="onboarding-progress"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Setup progress"
      >
        <div className="onboarding-progress-fill" style={{ width: `${pct}%` }} />
      </div>

      <Card className="auth-card wizard-step-card">
        <h2 className="wizard-step-title">{step.title}</h2>
        <p className="wizard-step-explain">{step.explain}</p>

        {step.declarationKey && data && (
          <Toggle
            label={step.declarationLabel || ""}
            checked={data.declarations[step.declarationKey]}
            onChange={(v) => saveDeclaration(step.declarationKey!, v)}
          />
        )}

        {step.inlineForm === "paycheck" && (
          <div className="wizard-inline-form">
            <QuickAddForm kind="paycheck" {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "cash_profile" && (
          <div className="wizard-inline-form">
            <CashProfileForm {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "bill" && (
          <div className="wizard-inline-form">
            <QuickAddForm kind="bill" {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "housing" && (
          <div className="wizard-inline-form">
            <HousingForm {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "debt" && (
          <div className="wizard-inline-form">
            <QuickAddForm kind="debt" {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "credit_card" && (
          <div className="wizard-inline-form">
            <QuickAddForm kind="credit_card" {...wizardFormProps} />
          </div>
        )}

        {step.inlineForm === "ai" && (
          <div className="wizard-inline-form">
            <AiSetupForm
              formId={WIZARD_FORM_ID}
              onSaved={onWizardSaved}
              onSaveFailed={onWizardSaveFailed}
            />
          </div>
        )}

        {step.id === "finish" && data?.setup_roadmap && (
          <ul className="setup-roadmap-list">
            {data.setup_roadmap.map((phase) => (
              <li key={phase.day} data-done={phase.complete ? "yes" : "no"}>
                <strong>Day {phase.day}:</strong> {phase.title}
                {!phase.complete && phase.available && (
                  <span className="field-hint"> — {phase.hint}</span>
                )}
                {phase.complete && <span className="field-hint"> ✓</span>}
              </li>
            ))}
          </ul>
        )}

        {!step.inlineForm &&
          secs.map((sec) => (
            <p
              key={sec.id}
              className="field-hint wizard-step-status"
              data-done={sec.complete || sec.skipped ? "yes" : "no"}
            >
              {sec.label}:{" "}
              {sec.id === "credit_score"
                ? sec.complete
                  ? "✓ Entered (optional)"
                  : "○ Optional"
                : sec.complete
                  ? "✓ Complete"
                  : sec.skipped
                    ? "✓ Skipped"
                    : "○ Not complete yet"}
            </p>
          ))}

        {step.moneyLink && !step.inlineForm && (
          <p className="wizard-step-link">
            <Link to={step.moneyLink.to} className="btn btn-secondary btn-sm">
              {step.moneyLink.label}
            </Link>
            <span className="field-hint wizard-step-link-hint">
              Return here after saving — progress updates when you continue or refresh.
            </span>
          </p>
        )}
      </Card>

      <WizardNav
        onBack={back}
        onRefresh={() => load()}
        onNext={next}
        backDisabled={stepIndex === 0}
        busy={saving}
        nextLabel={
          step.id === "finish"
            ? data?.good_enough_ready
              ? "Start my dashboard"
              : "Finish setup"
            : step.id === "ai"
              ? "Skip for now"
              : "Next step"
        }
      />
    </AuthScreen>
  );
}
