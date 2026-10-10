import { useState } from "react";
import { api, type HouseholdPayload } from "../api";
import { Btn, Card, Field } from "./ui";

type Row = Record<string, unknown>;
type Kind = "member" | "income" | "vehicle" | "coverage" | "risk" | "event";
type Props = { household: HouseholdPayload; onReload: () => Promise<void> };

const text = (row: Row, key: string, fallback = "") => String(row[key] ?? fallback);
const number = (row: Row, key: string) => String(row[key] ?? "");
const checked = (row: Row, key: string) => Boolean(row[key]);

function EditFields({ kind, values, setValue }: { kind: Kind; values: Row; setValue: (key: string, value: unknown) => void }) {
  const input = (label: string, key: string, type: "text" | "number" | "date" = "text", placeholder?: string) => (
    <Field label={label} key={key}><input type={type} value={type === "number" ? number(values, key) : text(values, key)} placeholder={placeholder} onChange={(e) => setValue(key, type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} /></Field>
  );
  const toggle = (label: string, key: string) => <label className="checkbox-field" key={key}><input type="checkbox" checked={checked(values, key)} onChange={(e) => setValue(key, e.target.checked)} /> {label}</label>;
  if (kind === "member") return <>{input("Name or label", "label")}{input("Age band", "age_band", "text", "adult, child, older adult")}{toggle("Adult", "is_adult")}{toggle("Financially dependent", "financially_dependent")}{toggle("Contributes income", "income_contributor")}{input("Recurring care cost", "recurring_care_cost", "number")}{input("Expected income interruption (months)", "expected_income_interruption_months", "number")}</>;
  if (kind === "income") return <>{input("Income type", "income_type", "text", "fixed, variable, seasonal")}{input("Stability", "stability_band", "text", "unlikely, possible, elevated, known change")}{input("Income share (0–1)", "income_share", "number")}{input("Affected portion (0–1)", "affected_percent", "number")}{input("Recovery period (months)", "recovery_duration_months", "number")}{input("Known reduction", "known_reduction", "number")}{input("Severance or benefit offset", "severance_or_benefit_offset", "number")}</>;
  if (kind === "vehicle") return <>{input("Year", "year", "number")}{input("Make", "make")}{input("Model", "model")}{input("Mileage", "mileage", "number")}{toggle("Required for income", "required_for_income")}{toggle("Alternate transportation available", "alternate_transport")}{toggle("Backup vehicle", "backup_vehicle")}{toggle("Under warranty", "warranty")}{input("Deductible", "deductible", "number")}{input("Repair exposure — low", "repair_exposure_low", "number")}{input("Repair exposure — high", "repair_exposure_high", "number")}</>;
  if (kind === "coverage") return <>{input("Coverage type", "coverage_type", "text", "health, auto, disability, renters")}{input("Status", "status", "text", "adequate, partial, none, unknown")}{input("Deductible", "deductible", "number")}{input("Out-of-pocket maximum", "out_of_pocket_max", "number")}{input("Income replacement (0–1)", "income_replacement_percent", "number")}{input("Waiting period (days)", "waiting_period_days", "number")}{input("Known coverage gap", "coverage_gap", "number")}</>;
  if (kind === "event") return <>{input("Event", "event_type")}{input("Date", "event_date", "date")}{input("Cost — low", "estimated_cost_low", "number")}{input("Cost — high", "estimated_cost_high", "number")}{input("Income impact", "income_impact", "number")}{input("Duration (months)", "duration_months", "number")}{input("Certainty", "certainty", "text", "expected, possible, unknown")}</>;
  return <>{input("Risk type", "risk_type")}{input("Likelihood", "likelihood_band", "text", "unlikely, possible, elevated")}{input("Impact — low", "impact_low", "number")}{input("Impact — high", "impact_high", "number")}{input("Income impact (0–1)", "income_impact_percent", "number")}{input("Duration (months)", "duration_months", "number")}{input("Horizon (months)", "horizon_months", "number")}{input("Mitigation", "mitigation")}{input("Confidence", "confidence", "text", "known, estimated, unknown")}</>;
}

export default function HouseholdRecords({ household, onReload }: Props) {
  const [coverageType, setCoverageType] = useState("");
  const [coverageLimit, setCoverageLimit] = useState("");
  const [eventType, setEventType] = useState("");
  const [eventCost, setEventCost] = useState("");
  const [healthCost, setHealthCost] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ kind: Kind; id: string; values: Row } | null>(null);
  const run = async (action: () => Promise<unknown>) => { setBusy(true); try { await action(); await onReload(); } finally { setBusy(false); } };
  const beginEdit = (kind: Kind, row: Row) => setEditing({ kind, id: String(row.id), values: { ...row } });
  const saveEdit = async () => {
    if (!editing) return;
    const { kind, id, values } = editing;
    await run(() => kind === "member" ? api.updateHouseholdMember(id, values) : kind === "income" ? api.updateHouseholdIncomeRisk(id, values) : kind === "vehicle" ? api.updateHouseholdVehicle(id, values) : kind === "coverage" ? api.updateHouseholdCoverage(id, values) : kind === "event" ? api.updateHouseholdLifeEvent(id, values) : api.updateHouseholdRisk(id, values));
    setEditing(null);
  };
  const edit = (kind: Kind, row: Row) => editing?.id === String(row.id) && editing.kind === kind ? <div className="inline-editor"><EditFields kind={kind} values={editing.values} setValue={(key, value) => setEditing({ ...editing, values: { ...editing.values, [key]: value } })} /><div className="button-row"><Btn disabled={busy} onClick={() => void saveEdit()}>Save</Btn><Btn variant="secondary" onClick={() => setEditing(null)}>Cancel</Btn></div></div> : null;
  const rowActions = (kind: Kind, row: Row, remove: () => Promise<unknown>) => <><Btn variant="secondary" disabled={busy} onClick={() => beginEdit(kind, row)}>Edit</Btn><Btn variant="secondary" disabled={busy} onClick={() => void run(remove)}>Remove</Btn></>;
  const list = (kind: Kind, rows: Row[], label: (row: Row) => string, remove: (row: Row) => Promise<unknown>) => rows.length ? rows.map((row) => <div key={String(row.id)}><div className="list-row"><span>{label(row)}</span><span className="button-row">{rowActions(kind, row, () => remove(row))}</span></div>{edit(kind, row)}</div>) : <p className="subtitle">Nothing recorded yet.</p>;
  return <div className="today-columns">
    <Card><h2>People & dependents</h2>{list("member", household.members, (row) => `${text(row, "label", "Household member")} · ${text(row, "age_band", "adult")}`, (row) => api.deleteHouseholdMember(String(row.id)))}</Card>
    <Card><h2>Income & work risks</h2>{list("income", household.income_risks, (row) => `${text(row, "income_type", "Income")} · ${text(row, "stability_band", "unknown")}`, (row) => api.deleteHouseholdIncomeRisk(String(row.id)))}</Card>
    <Card><h2>Vehicles & transport</h2>{list("vehicle", household.vehicles, (row) => `${text(row, "year")} ${text(row, "make")} ${text(row, "model")}`.trim() || "Vehicle", (row) => api.deleteHouseholdVehicle(String(row.id)))}</Card>
    <Card><h2>Upcoming events</h2>{list("event", household.life_events, (row) => `${text(row, "event_type", "Event")} · ${text(row, "event_date", "date not set")}`, (row) => api.deleteHouseholdLifeEvent(String(row.id)))}</Card>
    <Card><h2>Insurance & coverage</h2><p className="subtitle">Add only coverage that applies to a household risk.</p><Field label="Type"><input value={coverageType} onChange={(e) => setCoverageType(e.target.value)} placeholder="Health, auto, disability, renters" /></Field><Field label="Out-of-pocket maximum"><input type="number" min="0" value={coverageLimit} onChange={(e) => setCoverageLimit(e.target.value)} /></Field><Btn disabled={busy || !coverageType} onClick={() => void run(() => api.addHouseholdCoverage({ coverage_type: coverageType, status: "adequate", out_of_pocket_max: Number(coverageLimit || 0) }))}>Add coverage</Btn>{list("coverage", household.coverage, (row) => `${text(row, "coverage_type", "Coverage")} · ${text(row, "status", "unknown")}`, (row) => api.deleteHouseholdCoverage(String(row.id)))}</Card>
    <Card><h2>Health & care costs</h2><p className="subtitle">Record financial exposure without entering a diagnosis.</p><Field label="Monthly care or medical cost"><input type="number" min="0" value={healthCost} onChange={(e) => setHealthCost(e.target.value)} /></Field><Btn disabled={busy || !healthCost} onClick={() => void run(() => api.addHouseholdRisk({ risk_type: "health", monthly_impact: Number(healthCost), duration_months: 12 }))}>Add care exposure</Btn>{list("risk", household.risks.filter((row) => String(row.risk_type || "").toLowerCase().includes("health")), () => "Health exposure", (row) => api.deleteHouseholdRisk(String(row.id)))}</Card>
    <Card><h2>Housing, legal & other risks</h2><p className="subtitle">For a required payment, use Money so dates and payment history stay authoritative.</p><Field label="Exposure type"><input value={eventType} onChange={(e) => setEventType(e.target.value)} placeholder="Housing or legal exposure" /></Field><Field label="Possible amount"><input type="number" min="0" value={eventCost} onChange={(e) => setEventCost(e.target.value)} /></Field><Btn disabled={busy || !eventType} onClick={() => void run(() => api.addHouseholdRisk({ risk_type: eventType, impact_high: Number(eventCost || 0), severity: "moderate" }))}>Add exposure</Btn>{list("risk", household.risks.filter((row) => !String(row.risk_type || "").toLowerCase().includes("health")), (row) => `${text(row, "risk_type", "Risk")} · ${text(row, "likelihood_band", "unknown")}`, (row) => api.deleteHouseholdRisk(String(row.id)))}</Card>
  </div>;
}
