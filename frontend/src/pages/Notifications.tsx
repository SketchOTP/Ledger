import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type NotificationItem } from "../api";
import NotificationPermissionCard from "../components/NotificationPermissionCard";
import { Btn, Card, EmptyState, PageHeader, Pill } from "../components/ui";

function NotificationRow({
  item,
  onDismiss,
  onSnooze,
  busy,
}: {
  item: NotificationItem;
  onDismiss: (id: string, reason: string) => void;
  onSnooze: (id: string, days: number) => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");

  return (
    <div className="list-row">
      <div className="list-row-body">
        <div className="list-row-title">{item.title}</div>
        <div className="list-row-sub">{item.body}</div>
        <Link to={item.link_path} style={{ fontSize: "0.85rem" }}>
          View →
        </Link>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem", alignItems: "flex-end" }}>
        <Pill>{item.severity}</Pill>
        {item.severity === "critical" && (
          <input
            className="input"
            placeholder="Dismiss reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            style={{ maxWidth: 140, fontSize: "0.8rem" }}
          />
        )}
        <div style={{ display: "flex", gap: "0.25rem" }}>
          <Btn
            variant="ghost"
            onClick={() => onSnooze(item.id, 1)}
            disabled={busy}
          >
            Snooze 1d
          </Btn>
          <Btn
            variant="ghost"
            onClick={() =>
              onDismiss(item.id, item.severity === "critical" ? reason : "dismissed")
            }
            disabled={busy || (item.severity === "critical" && !reason.trim())}
          >
            Dismiss
          </Btn>
        </div>
      </div>
    </div>
  );
}

export default function Notifications() {
  const [inbox, setInbox] = useState<Awaited<ReturnType<typeof api.notificationsInbox>> | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showDismissed, setShowDismissed] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setErr("");
    api
      .notificationsInbox(showDismissed, true)
      .then(setInbox)
      .catch((e) => setErr(e instanceof Error ? e.message : "Could not load notifications"))
      .finally(() => setLoading(false));
  }, [showDismissed]);

  useEffect(() => {
    load();
  }, [load]);

  const dismiss = async (id: string, reason: string) => {
    setBusy(true);
    try {
      await api.dismissNotification(id, reason);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Dismiss failed");
    } finally {
      setBusy(false);
    }
  };

  const snooze = async (id: string, days: number) => {
    setBusy(true);
    try {
      await api.snoozeNotification(id, days);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Snooze failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Notifications"
        subtitle="Advisory alerts only — review and act yourself. No automatic money movement."
      />

      <NotificationPermissionCard />

      <div className="form-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
        <Btn onClick={load} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh inbox"}
        </Btn>
        <label style={{ display: "flex", alignItems: "center", gap: "0.35rem", fontSize: "0.9rem" }}>
          <input
            type="checkbox"
            checked={showDismissed}
            onChange={(e) => setShowDismissed(e.target.checked)}
          />
          Show dismissed
        </label>
      </div>

      {err && <p className="error">{err}</p>}

      {inbox && (
        <>
          <Card>
            <h3 style={{ marginTop: 0 }}>Critical ({inbox.counts.critical})</h3>
            <div className="list-group">
              {inbox.critical.map((n) => (
                <NotificationRow
                  key={n.id}
                  item={n}
                  onDismiss={dismiss}
                  onSnooze={snooze}
                  busy={busy}
                />
              ))}
            </div>
            {!inbox.critical.length && (
              <p style={{ color: "var(--muted)", margin: 0 }}>No critical alerts.</p>
            )}
          </Card>

          <Card>
            <h3 style={{ marginTop: 0 }}>Due this week</h3>
            <div className="list-group">
              {inbox.due_this_week.map((n) => (
                <NotificationRow
                  key={n.id}
                  item={n}
                  onDismiss={dismiss}
                  onSnooze={snooze}
                  busy={busy}
                />
              ))}
            </div>
            {!inbox.due_this_week.length && (
              <EmptyState message="Nothing urgent this week." />
            )}
          </Card>

          <Card>
            <h3 style={{ marginTop: 0 }}>Snoozed</h3>
            {inbox.snoozed.length ? (
              <div className="list-group">
                {inbox.snoozed.map((n) => (
                  <div className="list-row" key={n.id}>
                    <div className="list-row-body">
                      <div className="list-row-title">{n.title}</div>
                      <div className="list-row-sub">Until {n.snoozed_until}</div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p style={{ color: "var(--muted)", margin: 0 }}>No snoozed items.</p>
            )}
          </Card>

          {showDismissed && (
            <Card>
              <h3 style={{ marginTop: 0 }}>Dismissed</h3>
              {inbox.dismissed.length ? (
                <ul>
                  {inbox.dismissed.map((n) => (
                    <li key={n.id}>
                      {n.title}
                      {n.dismiss_reason ? ` — ${n.dismiss_reason}` : ""}
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={{ color: "var(--muted)", margin: 0 }}>None dismissed.</p>
              )}
            </Card>
          )}
        </>
      )}
    </>
  );
}
