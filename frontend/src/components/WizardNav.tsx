import { Btn } from "./ui";

function IconBack() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M15 6l-6 6 6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconRefresh() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 12a8 8 0 0 1 13.5-5.7M20 7v4h-4M20 12a8 8 0 0 1-13.5 5.7M4 17v-4h4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconNext() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M9 6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function WizardNav({
  onBack,
  onRefresh,
  onNext,
  backDisabled,
  busy,
  nextLabel = "Next step",
}: {
  onBack: () => void;
  onRefresh: () => void;
  onNext: () => void;
  backDisabled?: boolean;
  busy?: boolean;
  nextLabel?: string;
}) {
  return (
    <nav className="wizard-step-actions" aria-label="Setup navigation">
      <Btn
        type="button"
        variant="ghost"
        onClick={onBack}
        disabled={backDisabled || busy}
        aria-label="Back"
        title="Back"
      >
        <IconBack />
      </Btn>
      <Btn
        type="button"
        variant="secondary"
        onClick={onRefresh}
        disabled={busy}
        aria-label="Refresh progress"
        title="Refresh"
      >
        <IconRefresh />
      </Btn>
      <Btn
        type="button"
        variant="primary"
        onClick={onNext}
        disabled={busy}
        aria-label={nextLabel}
        title={nextLabel}
      >
        <IconNext />
      </Btn>
    </nav>
  );
}
