import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthScreen } from "../components/AuthScreen";
import { Btn, Card, Field, Input, Toggle } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { rememberMeEnabled } from "../auth/tokenStorage";

export default function Login() {
  const { login } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [rememberMe, setRememberMe] = useState(true);

  useEffect(() => {
    setRememberMe(rememberMeEnabled());
  }, []);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    const fd = new FormData(e.currentTarget);
    try {
      const needsSetup = await login(
        String(fd.get("username")),
        String(fd.get("password")),
        rememberMe
      );
      nav(needsSetup ? "/onboarding" : "/", { replace: true });
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthScreen title="Sign in" subtitle="Your household CFO — private on this device">
      <Card className="auth-card">
        <form onSubmit={submit}>
          <Field label="Username">
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
          <Field label="PIN or password">
            <Input
              name="password"
              type="password"
              required
              autoComplete="current-password"
              minLength={4}
            />
          </Field>
          <Toggle
            label="Remember me on this device"
            checked={rememberMe}
            onChange={setRememberMe}
            hint="Recommended for local use — stay signed in after you close the app."
          />
          {err && <p className="error">{err}</p>}
          <Btn type="submit" full disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Btn>
        </form>
        <p className="auth-card-footer">
          New here? <Link to="/register">Create an account</Link>
        </p>
      </Card>
    </AuthScreen>
  );
}
