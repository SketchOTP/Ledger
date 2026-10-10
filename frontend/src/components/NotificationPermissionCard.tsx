import { useCallback, useEffect, useState } from "react";
import {
  enableBrowserNotifications,
  getNotificationSetupState,
  notificationsUserEnabled,
  pushSupported,
  sendTestNotification,
  type NotificationSetupState,
} from "../lib/browserNotifications";
import { Btn, Card } from "./ui";

export default function NotificationPermissionCard() {
  const [state, setState] = useState<NotificationSetupState>(() => getNotificationSetupState());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const refresh = useCallback(() => {
    setState(getNotificationSetupState());
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const enable = async () => {
    setBusy(true);
    setMsg("");
    try {
      const next = await enableBrowserNotifications();
      setState(next);
      if (next === "granted" || next === "granted_no_sw") {
        await sendTestNotification();
        setMsg(
          next === "granted"
            ? "Enabled — check your phone for a test notification (works when the app is closed)."
            : "Permission granted, but Web Push is unavailable on this browser — alerts only while open."
        );
      } else if (next === "denied") {
        setMsg("Blocked in browser settings. On Android: Site settings → Notifications → Allow.");
      } else if (next === "unsupported") {
        setMsg("This browser does not support notifications.");
      } else {
        setMsg("Permission not granted yet.");
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Could not enable notifications");
    } finally {
      setBusy(false);
    }
  };

  const enabled = state === "granted" || state === "granted_no_sw" || notificationsUserEnabled();

  return (
    <Card className="notification-permission-card">
      <h3 style={{ marginTop: 0 }}>Browser &amp; app notifications</h3>
      <p className="field-hint" style={{ marginBottom: "0.75rem" }}>
        Required for bill-due alerts on this device — including when installed as a PWA on Android.
        Uses Web Push so alerts arrive while the app is closed. Tap the button below (Android will
        show the system Allow prompt).
      </p>

      {!pushSupported() && state !== "unsupported" && (
        <p className="field-hint" style={{ marginBottom: "0.75rem" }}>
          This browser does not support Web Push — alerts only appear while Ledger is open.
        </p>
      )}

      {state === "unsupported" && (
        <p className="error">Notifications are not supported in this browser.</p>
      )}

      {state === "denied" && (
        <p className="error">
          Notifications are blocked. Open Chrome → site settings → Notifications → Allow for this
          site, then return here.
        </p>
      )}

      {enabled ? (
        <>
          <p style={{ color: "var(--safe)", margin: "0 0 0.75rem" }}>
            Notifications are enabled on this device.
          </p>
          <Btn variant="secondary" disabled={busy} onClick={() => void sendTestNotification()}>
            Send test phone notification
          </Btn>
        </>
      ) : (
        <Btn disabled={busy || state === "unsupported"} onClick={() => void enable()}>
          {busy ? "Waiting for permission…" : "Allow notifications"}
        </Btn>
      )}

      {msg && <p style={{ marginTop: "0.75rem", fontSize: "0.9rem" }}>{msg}</p>}
    </Card>
  );
}
