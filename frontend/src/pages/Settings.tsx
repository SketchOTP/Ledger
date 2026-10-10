import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Card, Field, IconMoon, IconSun, Input, PageHeader, Pill } from "../components/ui";
import { api, type AISettingsPayload } from "../api";
import { useTheme } from "../theme/ThemeContext";

const RESEARCH_KEYS = [
  {
    key: "FRED_API_KEY",
    label: "FRED",
    desc: "Mortgage rates, macro series, loan benchmarks",
  },
  {
    key: "BLS_API_KEY",
    label: "BLS",
    desc: "CPI trends for food, utilities, and inflation",
  },
  {
    key: "EIA_API_KEY",
    label: "EIA",
    desc: "State electricity and utility price benchmarks",
  },
  {
    key: "USDA_ERS_API_KEY",
    label: "USDA ERS",
    desc: "Food price outlook (optional — fixtures if unset)",
  },
] as const;

function statusTone(status: string): "safe" | "tight" | "danger" | "neutral" {
  if (status === "configured") return "safe";
  if (status === "unavailable") return "tight";
  if (status === "misconfigured") return "danger";
  return "neutral";
}

function previewOrUnset(preview: string | null | undefined, configured: boolean) {
  if (preview) return <code>{preview}</code>;
  return configured ? "Configured" : "Not set";
}

export default function Settings() {
  const nav = useNavigate();
  const { logout, refreshMe } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const [data, setData] = useState<AISettingsPayload | null>(null);
  const [researchFields, setResearchFields] = useState<
    { key: string; configured: boolean; preview?: string | null }[]
  >([]);
  const [err, setErr] = useState("");
  const [savingAi, setSavingAi] = useState(false);
  const [savingKeys, setSavingKeys] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [resetConfirm, setResetConfirm] = useState("");
  const [resetMsg, setResetMsg] = useState("");
  const [resetErr, setResetErr] = useState("");
  const [resetting, setResetting] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [researchKeyInputs, setResearchKeyInputs] = useState<Record<string, string>>({});
  const [form, setForm] = useState({
    ai_provider: "",
    ai_base_url: "",
    ai_model: "",
    ai_reasoning_effort: "high",
    assistant_model: "gpt-6-luna",
    assistant_reasoning_effort: "high",
    ai_allow_remote_openai: false,
  });

  const load = () =>
    Promise.all([api.aiSettings(), api.researchSettings()])
      .then(([ai, research]) => {
        setData(ai);
        setResearchFields(research.fields);
        setForm({
          ai_provider: ai.provider,
          ai_base_url: ai.base_url || "",
          ai_model: ai.model || "",
          ai_reasoning_effort: ai.reasoning_effort || "high",
          assistant_model: ai.assistant_model || "gpt-6-luna",
          assistant_reasoning_effort: ai.assistant_reasoning_effort || "high",
          ai_allow_remote_openai: ai.remote_allowed,
        });
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "Could not load settings"));

  useEffect(() => {
    load();
  }, []);

  const selectedProvider = useMemo(
    () => data?.registry.find((p) => p.id === form.ai_provider),
    [data?.registry, form.ai_provider]
  );

  const researchByKey = useMemo(
    () => Object.fromEntries(researchFields.map((f) => [f.key, f])),
    [researchFields]
  );

  const saveAi = async () => {
    setSavingAi(true);
    setErr("");
    try {
      await api.saveAiSettings({
        ai_provider: form.ai_provider,
        ai_base_url: form.ai_base_url || undefined,
        ai_model: form.ai_model || undefined,
        ai_reasoning_effort: form.ai_reasoning_effort,
        assistant_model: form.assistant_model,
        assistant_reasoning_effort: form.assistant_reasoning_effort,
        ai_allow_remote_openai: form.ai_allow_remote_openai,
        ai_api_key: apiKeyInput || undefined,
      });
      setApiKeyInput("");
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save AI settings");
    } finally {
      setSavingAi(false);
    }
  };

  const saveResearchKeys = async () => {
    setSavingKeys(true);
    setErr("");
    try {
      await api.saveResearchSettings(researchKeyInputs);
      setResearchKeyInputs({});
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not save API keys");
    } finally {
      setSavingKeys(false);
    }
  };

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
      nav("/login", { replace: true });
    } finally {
      setLoggingOut(false);
    }
  };

  const resetHousehold = async () => {
    setResetErr("");
    setResetMsg("");
    setResetting(true);
    try {
      const res = await api.resetHousehold(resetConfirm);
      if (!res.ok) {
        setResetErr(res.error || "Reset failed");
        return;
      }
      setResetMsg(res.message || "Data reset.");
      setResetConfirm("");
      await refreshMe();
      nav("/onboarding", { replace: true });
    } catch (e) {
      setResetErr(e instanceof Error ? e.message : "Reset failed");
    } finally {
      setResetting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Settings"
        subtitle="AI provider, model, and API keys used across Ledger"
        action={
          <div className="page-header-actions">
            <button
              type="button"
              className="theme-toggle-btn"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              title={theme === "dark" ? "Light mode" : "Dark mode"}
            >
              {theme === "dark" ? <IconSun /> : <IconMoon />}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => void handleLogout()}
              disabled={loggingOut}
            >
              {loggingOut ? "Signing out…" : "Log out"}
            </button>
          </div>
        }
      />

      {err && <p className="error">{err}</p>}

      {data && (
        <>
          <Card>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}>
              <h3 style={{ margin: 0 }}>AI provider</h3>
              <Pill tone={statusTone(data.status)}>{data.status}</Pill>
            </div>
            <dl className="ai-settings-dl">
              <dt>Provider</dt>
              <dd>{data.provider_label || data.provider}</dd>
              <dt>Model</dt>
              <dd>{data.model || "—"}</dd>
              <dt>Base URL</dt>
              <dd>{data.base_url || "—"}</dd>
              <dt>API key</dt>
              <dd>{previewOrUnset(data.api_key_preview, data.api_key_configured)}</dd>
              <dt>Remote OpenAI</dt>
              <dd>{data.remote_allowed ? "Allowed" : "Blocked"}</dd>
            </dl>
          </Card>

          {data.warnings.length > 0 && (
            <Card>
              <h3>Warnings</h3>
              <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
                {data.warnings.map((w, i) => (
                  <li key={i} style={{ marginBottom: "0.35rem" }}>
                    {w}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {data.auth_mode === "api_key" &&
            data.base_url.includes("api.openai.com") &&
            !data.remote_allowed && (
              <div className="plain-box" style={{ borderColor: "var(--danger)" }}>
                <strong>Remote cloud blocked</strong>
                <p style={{ margin: "0.5rem 0 0", fontSize: "0.9rem" }}>
                  OpenAI cloud requires enabling &quot;Allow OpenAI cloud&quot; below.
                </p>
              </div>
            )}

          <Card>
            <h3>AI health check</h3>
            {data.health_check?.ok ? (
              <p className="success-toast" style={{ margin: 0 }}>
                Provider reachable
                {Array.isArray(data.health_check.models) && data.health_check.models.length > 0
                  ? ` · models: ${(data.health_check.models as string[]).slice(0, 3).join(", ")}`
                  : ""}
              </p>
            ) : (
              <p className="error" style={{ margin: 0 }}>
                {String(data.health_check?.error || "Unavailable")}
              </p>
            )}
          </Card>

          <Card>
            <h3 style={{ marginTop: 0 }}>AI provider &amp; model</h3>
            <div className="ai-settings-dl">
              <label className="field">
                <span className="field-label">Provider</span>
                <select
                  className="input"
                  value={form.ai_provider}
                  onChange={(e) => setForm((s) => ({ ...s, ai_provider: e.target.value,
                    ai_model: e.target.value === "future_cli_codex" ? "gpt-6-luna" : s.ai_model,
                    ai_base_url: e.target.value === "future_cli_codex" ? "" : s.ai_base_url }))}
                >
                  {data.registry.filter((p) => p.implemented).map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>

              {form.ai_provider === "future_cli_codex" ? <><label className="field"><span className="field-label">Ledger AI model</span><select className="input" value={form.ai_model} onChange={(e) => setForm((s) => ({ ...s, ai_model: e.target.value }))}>{data.codex_models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label><label className="field"><span className="field-label">Ledger reasoning effort</span><select className="input" value={form.ai_reasoning_effort} onChange={(e) => setForm((s) => ({ ...s, ai_reasoning_effort: e.target.value }))}>{data.codex_reasoning_efforts.map((effort) => <option key={effort} value={effort}>{effort}</option>)}</select></label></> : <label className="field">
                <span className="field-label">Model</span>
                <input
                  className="input"
                  value={form.ai_model}
                  onChange={(e) => setForm((s) => ({ ...s, ai_model: e.target.value }))}
                  placeholder="gpt-4.1-mini / llama3.1 / etc"
                />
              </label>}

              <h4>Financial assistant</h4>
              <p className="field-hint">The chat on Today uses your Codex sign-in, separately from Ledger’s research model. These choices stay saved until you change them.</p>
              <label className="field"><span className="field-label">Assistant model</span><select className="input" value={form.assistant_model} onChange={(e) => setForm((s) => ({ ...s, assistant_model: e.target.value }))}>{data.codex_models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
              <label className="field"><span className="field-label">Assistant reasoning effort</span><select className="input" value={form.assistant_reasoning_effort} onChange={(e) => setForm((s) => ({ ...s, assistant_reasoning_effort: e.target.value }))}>{data.codex_reasoning_efforts.map((effort) => <option key={effort} value={effort}>{effort}</option>)}</select></label>

              {form.ai_provider !== "future_cli_codex" && <><label className="field">
                <span className="field-label">Base URL</span>
                <input
                  className="input"
                  value={form.ai_base_url}
                  onChange={(e) => setForm((s) => ({ ...s, ai_base_url: e.target.value }))}
                  placeholder="https://api.openai.com/v1"
                />
              </label>

              <label className="field">
                <span className="field-label">AI API key</span>
                <input
                  className="input"
                  type="password"
                  autoComplete="off"
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder={
                    data.api_key_preview
                      ? `${data.api_key_preview} — leave blank to keep`
                      : data.api_key_configured
                        ? "Leave blank to keep existing key"
                        : "sk-..."
                  }
                />
              </label>

              <label className="field">
                <span className="field-label">Allow OpenAI cloud</span>
                <input
                  type="checkbox"
                  checked={form.ai_allow_remote_openai}
                  onChange={(e) =>
                    setForm((s) => ({ ...s, ai_allow_remote_openai: e.target.checked }))
                  }
                />
              </label></>}

              <button type="button" className="btn btn-primary" disabled={savingAi} onClick={saveAi}>
                {savingAi ? "Saving…" : "Save AI settings"}
              </button>
            </div>
            {selectedProvider && !selectedProvider.implemented && (
              <p className="field-hint" style={{ marginTop: "0.5rem" }}>
                Selected provider is listed but not fully implemented yet.
              </p>
            )}
          </Card>

          <Card>
            <h3 style={{ marginTop: 0 }}>API keys</h3>
            <p className="field-hint">
              Keys are saved per account. Values are truncated after saving — enter a new key to
              replace, or leave blank to keep the current one.
            </p>
            {RESEARCH_KEYS.map(({ key, label, desc }) => {
              const field = researchByKey[key];
              const hintParts: string[] = [desc];
              if (field?.preview) hintParts.push(`Current: ${field.preview}`);
              else if (field?.configured) hintParts.push("Configured");
              return (
                <Field key={key} label={`${label} (${key})`} hint={hintParts.join(" · ")}>
                  <Input
                    type="password"
                    autoComplete="off"
                    placeholder={
                      field?.preview
                        ? `${field.preview} — leave blank to keep`
                        : field?.configured
                          ? "•••• configured"
                          : "Enter key"
                    }
                    value={researchKeyInputs[key] ?? ""}
                    onChange={(e) =>
                      setResearchKeyInputs((prev) => ({ ...prev, [key]: e.target.value }))
                    }
                  />
                </Field>
              );
            })}
            <button
              type="button"
              className="btn btn-secondary"
              disabled={savingKeys}
              onClick={saveResearchKeys}
            >
              {savingKeys ? "Saving…" : "Save API keys"}
            </button>
          </Card>
        </>
      )}

      <Card className="account-reset-card">
        <h3 style={{ marginTop: 0 }}>Reset my data</h3>
        <p style={{ fontSize: "0.9rem", color: "var(--muted)", lineHeight: 1.5 }}>
          Removes all household financial data and setup progress for this account only. Type{" "}
          <strong>RESET</strong> to confirm.
        </p>
        <input
          className="input"
          value={resetConfirm}
          onChange={(e) => setResetConfirm(e.target.value)}
          placeholder="RESET"
          aria-label="Confirm reset"
          style={{ maxWidth: 200, marginBottom: "0.5rem" }}
        />
        {resetErr && <p className="error">{resetErr}</p>}
        {resetMsg && <p style={{ color: "var(--safe)" }}>{resetMsg}</p>}
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => void resetHousehold()}
          disabled={resetting || resetConfirm !== "RESET"}
        >
          {resetting ? "Resetting…" : "Erase household data"}
        </button>
      </Card>
    </>
  );
}
