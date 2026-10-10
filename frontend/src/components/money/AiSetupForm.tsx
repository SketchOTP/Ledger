import { FormEvent, useEffect, useState } from "react";
import { Field, Input, Segmented, Select, Toggle } from "../ui";
import { api, type AISettingsPayload } from "../../api";

type AiMode = "local" | "api" | "cli";

const LOCAL_PROVIDERS = [
  { id: "lmstudio", label: "LM Studio" },
  { id: "mock", label: "Offline mock (testing)" },
];

const API_PROVIDERS = [
  { id: "openai", label: "OpenAI" },
  { id: "groq", label: "Groq" },
  { id: "openrouter", label: "OpenRouter" },
  { id: "routerbot", label: "Routerbot" },
  { id: "openai_compatible", label: "Other OpenAI-compatible" },
];

const CLI_PROVIDERS = [
  { id: "future_cli_cursor", label: "Cursor CLI (planned)" },
  { id: "future_cli_gemini", label: "Gemini CLI (planned)" },
  { id: "future_cli_claude", label: "Claude Code CLI (planned)" },
  { id: "future_cli_codex", label: "Codex CLI (planned)" },
];

function modeForProvider(id: string): AiMode {
  if (id.startsWith("future_cli_")) return "cli";
  if (id === "lmstudio" || id === "mock") return "local";
  return "api";
}

export function AiSetupForm({
  formId,
  onSaved,
  onSaveFailed,
}: {
  formId: string;
  onSaved: () => void;
  onSaveFailed?: () => void;
}) {
  const [mode, setMode] = useState<AiMode>("local");
  const [provider, setProvider] = useState("lmstudio");
  const [baseUrl, setBaseUrl] = useState("http://localhost:1234/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("local-model");
  const [allowRemote, setAllowRemote] = useState(false);
  const [err, setErr] = useState("");
  const [status, setStatus] = useState<AISettingsPayload | null>(null);

  useEffect(() => {
    api
      .aiSettings()
      .then((s) => {
        setStatus(s);
        const pid = s.provider || "lmstudio";
        setProvider(pid);
        setMode(modeForProvider(pid));
        setBaseUrl(s.base_url || "http://localhost:1234/v1");
        setModel(s.model || "local-model");
        setAllowRemote(s.remote_allowed);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (mode === "local") {
      if (!LOCAL_PROVIDERS.some((p) => p.id === provider)) {
        setProvider("lmstudio");
        setBaseUrl("http://localhost:1234/v1");
        setModel("local-model");
      }
    } else if (mode === "api") {
      if (!API_PROVIDERS.some((p) => p.id === provider)) {
        setProvider("openai");
        setBaseUrl("https://api.openai.com/v1");
      }
    } else if (!CLI_PROVIDERS.some((p) => p.id === provider)) {
      setProvider("future_cli_cursor");
    }
  }, [mode, provider]);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErr("");
    if (mode === "cli") {
      setErr("CLI providers are not available yet. Choose Local or Cloud API.");
      onSaveFailed?.();
      return;
    }
    try {
      const saved = await api.saveAiSettings({
        ai_provider: provider,
        ai_base_url: baseUrl,
        ai_api_key: apiKey,
        ai_model: model,
        ai_allow_remote_openai: allowRemote,
      });
      setStatus(saved);
      onSaved();
    } catch (ex) {
      const msg = ex instanceof Error ? ex.message : "Could not save AI settings";
      setErr(msg);
      onSaveFailed?.();
    }
  };

  return (
    <form id={formId} className="ai-setup-form quick-add-form" onSubmit={submit}>
      <Segmented
        options={[
          { value: "local", label: "Local" },
          { value: "api", label: "Cloud API" },
          { value: "cli", label: "CLI" },
        ]}
        value={mode}
        onChange={setMode}
      />

      {mode === "local" && (
        <>
          <Field label="Provider">
            <Select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
            >
              {LOCAL_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
          {provider === "lmstudio" && (
            <>
              <Field label="Base URL">
                <Input
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="http://localhost:1234/v1"
                  required
                />
              </Field>
              <Field label="Model name">
                <Input value={model} onChange={(e) => setModel(e.target.value)} required />
              </Field>
            </>
          )}
        </>
      )}

      {mode === "api" && (
        <>
          <Field label="Provider">
            <Select
              value={provider}
              onChange={(e) => {
                const id = e.target.value;
                setProvider(id);
                if (id === "openai") setBaseUrl("https://api.openai.com/v1");
                else if (id === "groq") setBaseUrl("https://api.groq.com/openai/v1");
                else if (id === "openrouter") setBaseUrl("https://openrouter.ai/api/v1");
              }}
            >
              {API_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Base URL">
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} required />
          </Field>
          <Field label="API key">
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={status?.api_key_configured ? "Leave blank to keep saved key" : "Required"}
              autoComplete="off"
            />
          </Field>
          <Field label="Model">
            <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o-mini" />
          </Field>
          <Toggle
            label="Allow OpenAI cloud (api.openai.com)"
            checked={allowRemote}
            onChange={setAllowRemote}
          />
        </>
      )}

      {mode === "cli" && (
        <>
          <p className="field-hint">
            CLI integrations (Cursor, Gemini, Claude, Codex) are planned. Use Local or Cloud API for
            now.
          </p>
          <Field label="Planned CLI">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {CLI_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Field>
        </>
      )}

      {status && (
        <p className="field-hint wizard-step-status" data-done={status.status === "configured" ? "yes" : "no"}>
          Status: {status.status}
          {status.health_check?.ok ? " · connected" : ""}
        </p>
      )}

      {err && <p className="error">{err}</p>}
    </form>
  );
}
