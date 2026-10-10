import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthScreen } from "../components/AuthScreen";
import { Btn, Card, Field, Input, Toggle } from "../components/ui";
import { useAuth } from "../auth/AuthContext";

export default function Register() {
  const { register } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [usePin, setUsePin] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const fd = new FormData(e.currentTarget);
    const password = String(fd.get("password"));
    if (password.length < 4) {
      setErr("PIN or password must be at least 4 characters");
      setBusy(false);
      return;
    }
    if (usePin && !/^\d{4,128}$/.test(password)) {
      setErr("PIN must be 4–128 digits only");
      setBusy(false);
      return;
    }
    try {
      await register(
        String(fd.get("username")),
        password,
        String(fd.get("display_name") || ""),
        rememberMe
      );
      nav("/onboarding", { replace: true });
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthScreen
      title="Create account"
      subtitle="Runs on this device only — a 4-digit PIN is fine if you prefer"
    >
      <Card className="auth-card">
        <form onSubmit={submit}>
          <Field label="Username" hint="3–32 characters: letters, numbers, underscore, hyphen">
            <Input
              name="username"
              type="text"
              required
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              minLength={3}
              maxLength={32}
              pattern="[a-zA-Z0-9][a-zA-Z0-9_-]{2,31}"
            />
          </Field>
          <Field label="Display name (optional)">
            <Input name="display_name" type="text" autoComplete="nickname" />
          </Field>
          <Toggle
            label="Use a 4-digit PIN instead of a password"
            checked={usePin}
            onChange={setUsePin}
            hint="PIN is stored hashed on this machine only."
          />
          <Field label={usePin ? "4-digit PIN (or longer)" : "PIN or password (4+ characters)"}>
            <Input
              name="password"
              type="password"
              inputMode={usePin ? "numeric" : "text"}
              pattern={usePin ? "[0-9]*" : undefined}
              autoComplete="new-password"
              required
              minLength={4}
              maxLength={128}
              placeholder={usePin ? "e.g. 1234" : ""}
            />
          </Field>
          <Toggle
            label="Remember me on this device"
            checked={rememberMe}
            onChange={setRememberMe}
            hint="Keeps you signed in after closing the browser. Uncheck for a shorter session."
          />
          {err && <p className="error">{err}</p>}
          <Btn type="submit" full disabled={busy}>
            {busy ? "Creating…" : "Create account"}
          </Btn>
        </form>
        <p className="auth-card-footer">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </Card>
    </AuthScreen>
  );
}
