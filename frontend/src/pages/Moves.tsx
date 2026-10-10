import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type V2Candidate, type V2CapabilityPayload, type V2CapabilityProfile, type V2DismissalReason, type V2MovesPayload, type V2ValidationPayload } from "../api";
import { Btn, BtnSubmit, Card, Field, Input, PageHeader, Pill, Segmented, Select } from "../components/ui";
import { candidateNextStep, candidateWhyNow, movesEmptyExplanation, movesEmptyState, movesPurpose, movesViewDescription, plainText } from "../lib/plainLanguage";

type View = "best" | "active" | "watching" | "history";
const labels: Record<V2DismissalReason, string> = { owner_irrelevant: "Not relevant", owner_too_much_work: "Too much work", owner_timing: "Bad timing", owner_capital: "Too much capital", owner_weak_evidence: "Evidence too weak", owner_other: "Other" };
const currency = (n: number | null) => n == null ? "Unknown" : `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

export default function Moves() {
  const navigate = useNavigate();
  const [moves, setMoves] = useState<V2MovesPayload | null>(null);
  const [profile, setProfile] = useState<V2CapabilityPayload | null>(null);
  const [view, setView] = useState<View>("best");
  const [selected, setSelected] = useState<V2Candidate | null>(null);
  const [dismissal, setDismissal] = useState<V2DismissalReason>("owner_other");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { const [m, p] = await Promise.all([api.v2Moves(), api.v2CapabilityProfile()]); setMoves(m); setProfile(p); setError(""); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load moves"); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const cards = useMemo(() => {
    const rows = moves?.candidates ?? [];
    if (view === "best") return (moves?.top_moves ?? []).filter(isRelevantCandidate);
    if (view === "active") return rows.filter((row) => ["accepted", "attempted"].includes(row.lifecycle_status));
    if (view === "history") return moves?.history ?? rows.filter((row) => ["dismissed", "rejected", "outcome_recorded"].includes(row.lifecycle_status));
    return rows.filter((row) => !["accepted", "attempted", "dismissed", "rejected", "outcome_recorded"].includes(row.lifecycle_status) && isRelevantCandidate(row));
  }, [moves, view]);
  const accept = async (candidate: V2Candidate) => {
    setBusy(true); try { await api.v2AcceptCandidate(candidate.id); await load(); setSelected(await api.v2Candidate(candidate.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not accept candidate"); } finally { setBusy(false); }
  };
  const dismiss = async (candidate: V2Candidate) => {
    setBusy(true); try { await api.v2DismissCandidate(candidate.id, dismissal); await load(); setSelected(await api.v2Candidate(candidate.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not dismiss candidate"); } finally { setBusy(false); }
  };
  const refresh = async () => { setBusy(true); try { await api.v2RefreshResearch("growth"); await load(); } catch (err) { setError(err instanceof Error ? err.message : "Could not refresh research"); } finally { setBusy(false); } };
  return <>
    <PageHeader title="Moves" subtitle="Ledger looks for worthwhile ways to improve your finances." action={<Btn disabled={busy} onClick={refresh}>Check for ways to improve</Btn>} />
    {error && <p className="error">{error}</p>}
    <Card className="moves-guide">
      <h2>What Moves is for</h2>
      <p>{movesPurpose()}</p>
      <div className="moves-guide-steps">
        <span><strong>Find</strong><small>Ledger looks for useful opportunities.</small></span>
        <span><strong>Check</strong><small>It checks the idea against your real finances.</small></span>
        <span><strong>Decide</strong><small>You choose whether to take the next step.</small></span>
      </div>
    </Card>
    <CapabilityPanel profile={profile} onChanged={load} />
    {moves?.advisory && <AdvisorySummary advisory={moves.advisory} onRefresh={async () => { setBusy(true); try { await api.refreshAdvisory(); await load(); } finally { setBusy(false); } }} />}
    {moves?.validation && <ResolveNext task={moves.validation as Record<string, unknown>} onOpenCandidate={(id) => void api.v2Candidate(id).then(setSelected).catch((err) => setError(err instanceof Error ? err.message : "Could not load validation task"))} onNavigate={navigate} />}
    {moves?.value && <Card><strong>What Ledger has helped with</strong><p className="subtitle">{String(moves.value.completed_outcomes ?? 0)} completed steps · ${Number(moves.value.observed_total_realized_value ?? 0).toLocaleString()} confirmed value</p></Card>}
    <Segmented value={view} onChange={setView} options={[{ value: "best", label: "Best" }, { value: "active", label: "In progress" }, { value: "watching", label: "Ideas" }, { value: "history", label: "Past" }]} />
    <p className="field-hint moves-view-description">{movesViewDescription(view)}</p>
    <div className="moves-layout"><section className="moves-list">{cards.length ? cards.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} onOpen={() => void api.v2Candidate(candidate.id).then(setSelected).catch((err) => setError(err instanceof Error ? err.message : "Could not load candidate"))} />) : <Card><h3>{movesEmptyState(view)}</h3><p className="subtitle">{movesEmptyExplanation(view, moves?.candidates?.length ?? 0)}</p>{view === "best" && (moves?.candidates?.length ?? 0) > 0 && <Btn variant="secondary" onClick={() => setView("watching")}>See ideas being checked</Btn>}</Card>}</section>
      {selected && <CandidateDetail candidate={selected} busy={busy} dismissal={dismissal} onDismissal={setDismissal} onAccept={() => void accept(selected)} onDismiss={() => void dismiss(selected)} />}
    </div>
  </>;
}

function AdvisorySummary({ advisory, onRefresh }: { advisory: NonNullable<V2MovesPayload["advisory"]>; onRefresh: () => Promise<void> }) {
  const allocation = advisory.wealth_plan?.allocation;
  return <Card className="moves-advisory"><div className="section-head"><div><h2>How Moves helps you build forward</h2><p className="subtitle">Ledger first protects the household, then separates genuinely free money into saving, growth tests, and money you keep available.</p></div><Btn variant="secondary" onClick={() => void onRefresh()}>Generate ideas</Btn></div>{allocation && <div className="move-facts"><span>Save {currency(allocation.save)}</span><span>Grow {currency(allocation.grow)}</span><span>Keep free {currency(allocation.free)}</span></div>}{advisory.hypotheses?.length ? <p className="field-hint">{advisory.hypotheses.length} household-specific idea{advisory.hypotheses.length === 1 ? "" : "s"} still need buyer and price evidence before they can be recommended.</p> : <p className="field-hint">No household-specific idea is proven yet. Generate a few bounded tests from your skills and reusable assets.</p>}</Card>;
}

function ResolveNext({ task, onOpenCandidate, onNavigate }: { task: Record<string, unknown>; onOpenCandidate: (id: string) => void; onNavigate: (path: string) => void }) {
  const resolved = (task.next_best_validation_task as Record<string, unknown> | undefined) || task;
  const instruction = candidateNextStep(String(resolved.instruction || task.next_task || "No owner validation task is currently prioritized."));
  const target = typeof resolved.target_route === "string" ? resolved.target_route : "";
  const candidateId = typeof resolved.candidate_id === "string" ? resolved.candidate_id : "";
  const taskType = String(resolved.task_type || "");
  const action = () => {
    if (target && target !== "/moves") onNavigate(target);
    else if (candidateId) onOpenCandidate(candidateId);
  };
  if (taskType === "HOUSEHOLD_PREREQUISITE" || taskType === "NO_ACTIONABLE_VALIDATION") return null;
  return <Card><div className="section-head"><div><strong>Next step</strong><p className="subtitle">{instruction}</p></div>{(target || candidateId) && <Btn variant="secondary" onClick={action}>{candidateId ? "Open idea" : "Open"}</Btn>}</div>{typeof resolved.candidates_helped === "number" && resolved.candidates_helped > 1 && <p className="field-hint">This can help several current ideas.</p>}</Card>;
}

function CandidateCard({ candidate, onOpen }: { candidate: V2Candidate; onOpen: () => void }) {
  const brief = candidate.decision_brief;
  return <Card onClick={onOpen}><div className="section-head"><div><h3>{plainText(candidate.title)}</h3><p className="subtitle">{plainText(candidate.summary || "Ledger found a possible way to improve your position.")}</p></div>{candidate.safety_eligible && <Pill tone="safe">Worth a look</Pill>}</div>
    <div className="move-facts"><span>{benefitText(candidate)}</span><span>Effort: {effortText(brief?.setup_hours ?? candidate.setup_hours)}</span><span>{candidateNextStep(brief?.next_step, brief?.missing_facts || [])}</span></div></Card>;
}

function CandidateDetail({ candidate, busy, dismissal, onDismissal, onAccept, onDismiss }: { candidate: V2Candidate; busy: boolean; dismissal: V2DismissalReason; onDismissal: (reason: V2DismissalReason) => void; onAccept: () => void; onDismiss: () => void }) {
  const brief = candidate.decision_brief;
  const canDecide = ["qualified", "proposed"].includes(candidate.lifecycle_status);
  return <aside className="candidate-detail"><Card><h2>{candidate.title}</h2><p>{plainText(candidate.summary || "No summary supplied.")}</p>{brief && <><section className="decision-brief"><h3>Why this could help</h3><p>{candidateWhyNow(brief.why_now, brief.missing_facts)}</p><h3>What you may gain</h3><p>{benefitText(candidate)}</p><h3>What it takes</h3><p>{effortText(brief.setup_hours)} to start{brief.recurring_hours_per_week != null ? `, then about ${hours(brief.recurring_hours_per_week, " hours a week")}` : ""}. {capitalText(brief.required_capital)}</p><h3>What could go wrong</h3><p>{downsideText(brief)}</p><h3>What to do next</h3><p className="decision-next">{candidateNextStep(brief.next_step, brief.missing_facts)}</p>{brief.missing_facts.length > 0 && <p className="validation-warnings">We need one more detail before we can tell if this is worth doing.</p>}</section><details><summary>How Ledger decided</summary><dl className="detail-grid"><dt>Value type</dt><dd>{brief.value_type}</dd><dt>Possible gain after known costs</dt><dd>{range(brief.expected_net_low, brief.expected_net_high)}</dd><dt>Confidence</dt><dd>{rangePercent(brief.probability_low, brief.probability_high)}</dd><dt>Money needed</dt><dd>{currency(brief.required_capital)}</dd><dt>Money you could lose</dt><dd>{currency(brief.capital_at_risk)}</dd><dt>Recurring cost</dt><dd>{currency(brief.recurring_operating_cost)}</dd><dt>Money unavailable</dt><dd>{days(brief.liquidity_lockup_days)}</dd><dt>Setup effort</dt><dd>{hours(brief.setup_hours)}</dd><dt>Recurring effort</dt><dd>{hours(brief.recurring_hours_per_week, "/week")}</dd><dt>Time to value</dt><dd>{days(brief.time_to_value_days)}</dd><dt>Downside severity</dt><dd>{score(brief.downside_severity)}</dd><dt>Capability fit</dt><dd>{score(brief.capability_fit_score)}</dd><dt>Scalability</dt><dd>{score(brief.scalability)}</dd><dt>Automation</dt><dd>{score(brief.automation_potential)}</dd><dt>Reversibility</dt><dd>{score(brief.reversibility)}</dd><dt>Complexity</dt><dd>{score(brief.complexity)}</dd><dt>Evidence</dt><dd>{brief.verification_status} · {brief.freshness_status}</dd><dt>Ranking</dt><dd>{brief.rank_score ?? "Unknown"}</dd>{candidate.rejection_reasons.length > 0 && <><dt>Why it is not ready</dt><dd>{candidate.rejection_reasons.map((reason) => plainText(reason)).join(" ")}</dd></>}</dl></details></>}
    {!canDecide && (candidate.category === "subscription" || candidate.category === "recurring" ? <RecurringConfirmation candidate={candidate} /> : <ValidationForm candidate={candidate} />)}
    {canDecide && <div className="decision-controls"><Btn disabled={busy || !candidate.safety_eligible} onClick={onAccept}>Accept and create action</Btn><label>Dismiss reason<select value={dismissal} onChange={(e) => onDismissal(e.target.value as V2DismissalReason)}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><Btn variant="secondary" disabled={busy} onClick={onDismiss}>Dismiss</Btn></div>}
    {candidate.lifecycle_status === "attempted" && <OutcomeForm candidate={candidate} />}
  </Card></aside>;
}

function RecurringConfirmation({ candidate }: { candidate: V2Candidate }) {
  const [message, setMessage] = useState("");
  const respond = async (response: "keep" | "cancel" | "switch" | "not_recurring" | "not_sure") => {
    try {
      const replacement = response === "switch" ? window.prompt("What would the replacement cost each time?", "") : null;
      if (response === "switch" && (replacement == null || replacement === "")) return;
      await api.v2RecurringResponse(candidate.id, { response, ...(replacement == null ? {} : { new_recurring_amount: Number(replacement) }) });
      setMessage(response === "cancel" ? "Saved. Ledger updated the recommendation." : "Thanks — Ledger updated this item.");
    } catch (err) { setMessage(err instanceof Error ? err.message : "Could not save your answer"); }
  };
  return <section className="validation-form"><h3>Do you still want this charge?</h3><p className="field-hint">It looks like this charge happens regularly.</p><div className="inline-edit-actions"><Btn type="button" onClick={() => void respond("keep")}>Keep it</Btn><Btn type="button" onClick={() => void respond("cancel")}>I don’t need it</Btn><Btn type="button" variant="secondary" onClick={() => void respond("switch")}>Switch to something cheaper</Btn><Btn type="button" variant="secondary" onClick={() => void respond("not_recurring")}>This isn’t recurring</Btn><Btn type="button" variant="secondary" onClick={() => void respond("not_sure")}>Not sure</Btn></div>{message && <p className="field-hint">{message}</p>}</section>;
}

function ValidationForm({ candidate }: { candidate: V2Candidate }) {
  const [contract, setContract] = useState<V2ValidationPayload | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");
  useEffect(() => { void api.v2CandidateValidation(candidate.id).then(setContract).catch(() => setContract(null)); }, [candidate.id]);
  if (!contract || contract.missing_fact_types.length === 0) return null;
  const specs = contract.required_facts.filter((row) => contract.missing_fact_types.includes(row.fact_type));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      for (const spec of specs) {
        const raw = values[spec.fact_type];
        if (raw === undefined || raw === "") continue;
        const value = spec.units === "boolean" ? raw === "true" : ["currency", "percent", "hours", "hours_per_week", "months", "days"].includes(spec.units) ? Number(raw) : raw;
        await api.v2SaveCandidateValidation(candidate.id, { fact_type: spec.fact_type, value, units: spec.units, source_type: "owner_entered" });
      }
      setMessage("Saved. Ledger updated the recommendation.");
    } catch (err) { setMessage(err instanceof Error ? err.message : "Could not save validation facts"); }
  };
  return <form className="validation-form" onSubmit={submit}><h3>Add one detail</h3><p className="field-hint">Ledger will do the calculations after you save what you know.</p>{specs.map((spec) => <Field key={spec.fact_type} label={validationLabel(spec)} hint={spec.required === false ? "Optional" : undefined}>{spec.units === "account" ? <Select value={values[spec.fact_type] || ""} onChange={(e) => setValues((current) => ({ ...current, [spec.fact_type]: e.target.value }))}><option value="">Choose an account</option>{(contract.allowed_inputs?.[spec.fact_type] || []).map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}</Select> : spec.units === "boolean" ? <Select value={values[spec.fact_type] || ""} onChange={(e) => setValues((current) => ({ ...current, [spec.fact_type]: e.target.value }))}><option value="">Choose…</option><option value="true">Yes</option><option value="false">No</option></Select> : <Input type={spec.units === "date" ? "date" : "number"} inputMode="decimal" placeholder={spec.units} value={values[spec.fact_type] || ""} onChange={(e) => setValues((current) => ({ ...current, [spec.fact_type]: e.target.value }))} />}</Field>)}<Btn type="submit">Save detail</Btn>{message && <p className="field-hint">{message}</p>}</form>;
}

function isRelevantCandidate(candidate: V2Candidate): boolean {
  const applicability = (candidate as V2Candidate & { applicability?: { status?: string } }).applicability;
  return applicability?.status !== "NOT_CURRENTLY_APPLICABLE" && !(applicability?.status === "PREREQUISITE_MISSING" && ["cash_yield_benchmark", "savings"].includes(candidate.category));
}
function benefitText(candidate: V2Candidate): string { return candidate.expected_net_low == null || candidate.expected_net_high == null ? "Benefit still needs to be confirmed" : `Could help by ${currency(candidate.expected_net_low)}–${currency(candidate.expected_net_high)}`; }
function effortText(value: number | null | undefined): string { if (value == null) return "Unknown"; if (value <= 1) return "Very little"; if (value <= 4) return "A little"; if (value <= 12) return "Moderate"; return "A lot"; }
function capitalText(value: number | null | undefined): string { return value == null ? "No money needed is known yet." : `Money needed: ${currency(value)}.`; }
function downsideText(brief: NonNullable<V2Candidate["decision_brief"]>): string { if (brief.capital_at_risk != null && brief.capital_at_risk > 0) return `You could lose up to ${currency(brief.capital_at_risk)} if it does not work.`; return "No material downside is known from the current information."; }
function validationLabel(spec: V2ValidationPayload["required_facts"][number]): string {
  const labels: Record<string, string> = {
    comparison_account_id: "Which account should we compare?",
    current_apy: "Current APY on the account",
    quoted_apr: "Interest rate on the offer",
    quoted_term_months: "Length of the offer",
    closing_costs: "Closing costs on the offer",
    transfer_fee_percent: "Transfer fee percentage",
    transfer_fee_fixed: "Transfer fee",
    promo_apr: "Promotional interest rate",
    promo_duration_months: "How long the promotional rate lasts",
    compensation: "Stated pay or budget",
  };
  return labels[spec.fact_type] || plainText(spec.description);
}

function range(low: number | null | undefined, high: number | null | undefined) {
  if (low == null || high == null) return "Unknown";
  return `${currency(low)}–${currency(high)}`;
}
function rangePercent(low: number | null | undefined, high: number | null | undefined) {
  if (low == null || high == null) return "Unknown";
  return `${Math.round(low * 100)}–${Math.round(high * 100)}%`;
}
function hours(value: number | null | undefined, suffix = "h") { return value == null ? "Unknown" : `${value}${suffix}`; }
function days(value: number | null | undefined) { return value == null ? "Unknown" : `${value} days`; }
function score(value: number | null | undefined) { return value == null ? "Unknown" : value.toFixed(2); }

function OutcomeForm({ candidate }: { candidate: V2Candidate }) {
  const [status, setStatus] = useState<"completed" | "failed" | "abandoned">("completed");
  const [net, setNet] = useState(""); const [hours, setHours] = useState(""); const [message, setMessage] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); try { await api.v2RecordOutcome(candidate.id, { outcome_status: status, actual_net_value: net === "" ? null : Number(net), actual_setup_hours: hours === "" ? null : Number(hours) }); setMessage("Outcome recorded. Unknown fields remain unknown."); } catch (err) { setMessage(err instanceof Error ? err.message : "Could not record outcome"); } };
  return <form onSubmit={submit} className="outcome-form"><h3>Record observed outcome</h3><Field label="Result"><select value={status} onChange={(e) => setStatus(e.target.value as typeof status)}><option value="completed">Completed</option><option value="failed">Failed</option><option value="abandoned">Abandoned</option></select></Field><Field label="Actual net value (optional)"><Input inputMode="decimal" value={net} onChange={(e) => setNet(e.target.value)} /></Field><Field label="Actual setup hours (optional)"><Input inputMode="decimal" value={hours} onChange={(e) => setHours(e.target.value)} /></Field><Btn type="submit">Save outcome</Btn>{message && <p className="field-hint">{message}</p>}</form>;
}

function CapabilityPanel({ profile, onChanged }: { profile: V2CapabilityPayload | null; onChanged: () => void }) {
  const [open, setOpen] = useState(false); const [skill, setSkill] = useState(""); const [asset, setAsset] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown>>({}); const [refused, setRefused] = useState(""); const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (!profile) return;
    setDraft({ ...profile.profile });
    setRefused(profile.refused_activities.join(", "));
  }, [profile]);
  if (!profile) return null;
  const set = (key: string, value: unknown) => setDraft((current) => ({ ...current, [key]: value }));
  const save = async (event: FormEvent) => { event.preventDefault(); setSaving(true); try { await api.v2UpdateCapabilityProfile({ ...(draft as Partial<V2CapabilityProfile>), refused_activities: refused.split(",").map((item) => item.trim()).filter(Boolean) }); onChanged(); setOpen(false); } finally { setSaving(false); } };
  const checkbox = (key: string, label: string) => <label className="profile-check"><input type="checkbox" checked={Boolean(draft[key])} onChange={(e) => set(key, e.target.checked)} />{label}</label>;
  const nullable = (key: string, label: string) => <Field label={label}><Select value={draft[key] == null ? "" : draft[key] ? "yes" : "no"} onChange={(e) => set(key, e.target.value === "" ? null : e.target.value === "yes")}><option value="">Unspecified</option><option value="yes">Yes</option><option value="no">No</option></Select></Field>;
  const readiness = profile.completeness?.status === "GROWTH_READY" ? "Ready to look for income ideas" : profile.completeness?.status === "GROWTH_LIMITED" ? "Add a skill or reusable asset if you want income ideas" : "Ready to protect and improve your finances";
  return <Card className="capability-panel"><div className="section-head"><div><h3>What you can do</h3><p className="subtitle">{profile.skills.length} skills · {profile.assets.length} reusable assets</p><p className="field-hint">{readiness}</p>{profile.completeness?.message && <p className="field-hint">{plainText(profile.completeness.message)}</p>}</div><Btn variant="secondary" onClick={() => setOpen(!open)}>{open ? "Close" : "Edit"}</Btn></div>{open && <div className="capability-edit"><form onSubmit={save}><div className="profile-grid"><Field label="Maximum setup hours"><Input type="number" min="0" step="0.5" value={String(draft.max_setup_hours ?? "")} onChange={(e) => set("max_setup_hours", e.target.value === "" ? null : Number(e.target.value))} /></Field><Field label="Recurring hours/week"><Input type="number" min="0" step="0.5" value={String(draft.max_recurring_hours_per_week ?? "")} onChange={(e) => set("max_recurring_hours_per_week", e.target.value === "" ? null : Number(e.target.value))} /></Field><Field label="Preferred effort"><Select value={String(draft.preferred_effort_level ?? "")} onChange={(e) => set("preferred_effort_level", e.target.value)}><option value="">Unspecified</option><option value="low">Low</option><option value="moderate">Moderate</option><option value="high">High</option></Select></Field><Field label="Desired time to value (days)"><Input type="number" min="0" value={String(draft.desired_time_to_value_days ?? "")} onChange={(e) => set("desired_time_to_value_days", e.target.value === "" ? null : Number(e.target.value))} /></Field><Field label="Minimum meaningful payoff"><Input type="number" min="0" step="1" value={String(draft.minimum_meaningful_payoff ?? "")} onChange={(e) => set("minimum_meaningful_payoff", e.target.value === "" ? null : Number(e.target.value))} /></Field><Field label="Maximum preferred capital"><Input type="number" min="0" step="1" value={String(draft.max_preferred_capital ?? "")} onChange={(e) => set("max_preferred_capital", e.target.value === "" ? null : Number(e.target.value))} /></Field>{nullable("customer_interaction_willingness", "Customer interaction")}{nullable("sales_outreach_willingness", "Sales outreach")}{nullable("physical_labor_willingness", "Physical labor")}</div><div className="profile-checks">{checkbox("prefers_async", "Prefer async work")}{checkbox("prefers_automated", "Prefer automated work")}{checkbox("prefers_low_touch", "Prefer low-touch work")}{checkbox("prefers_recurring_income", "Prefer recurring income")}{checkbox("prefers_scalable_income", "Prefer scalable income")}{checkbox("allows_one_time_income", "Allow one-time income")}</div><Field label="Refused activities" hint="Structured activity names only; private notes stay local."><Input value={refused} onChange={(e) => setRefused(e.target.value)} placeholder="Comma-separated activities" /></Field><div className="inline-edit-actions"><Btn type="button" variant="secondary" onClick={() => setOpen(false)}>Cancel</Btn><BtnSubmit disabled={saving}>{saving ? "Saving…" : "Save profile"}</BtnSubmit></div></form><form onSubmit={(e) => { e.preventDefault(); if (skill.trim()) void api.v2AddSkill({ name: skill.trim() }).then(() => { setSkill(""); onChanged(); }); }}><Input aria-label="Skill name" placeholder="Add skill" value={skill} onChange={(e) => setSkill(e.target.value)} /><Btn type="submit">Add skill</Btn></form><form onSubmit={(e) => { e.preventDefault(); if (asset.trim()) void api.v2AddAsset({ name: asset.trim() }).then(() => { setAsset(""); onChanged(); }); }}><Input aria-label="Asset name" placeholder="Add reusable asset" value={asset} onChange={(e) => setAsset(e.target.value)} /><Btn type="submit">Add asset</Btn></form></div>}</Card>;
}
