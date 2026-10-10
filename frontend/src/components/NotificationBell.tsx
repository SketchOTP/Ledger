import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type NotificationItem, type NotificationsInboxPayload } from "../api";
import { clearNotifiedId, notifyNewItems } from "../lib/browserNotifications";
import { IconClose } from "./ui";

function IconBell({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 7h18s-3 0-3-7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M13.73 21a2 2 0 0 1-3.46 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export default function NotificationBell() {
  const [open, setOpen] = useState(false);
  const [inbox, setInbox] = useState<NotificationsInboxPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.notificationsInbox(false, true);
      setInbox(data);
      const active = [...data.critical, ...data.due_this_week, ...data.active];
      await notifyNewItems(
        active.map((n) => ({
          id: n.id,
          title: n.title,
          body: n.body,
          link_path: n.link_path,
        }))
      );
    } catch {
      /* non-fatal */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const dismiss = async (item: NotificationItem) => {
    const reason = item.severity === "critical" ? "Acknowledged from home" : "Dismissed from home";
    await api.dismissNotification(item.id, reason);
    clearNotifiedId(item.id);
    await load();
  };

  const count = inbox?.counts?.active ?? 0;
  const snippets = inbox
    ? [...inbox.critical, ...inbox.due_this_week, ...inbox.active].slice(0, 8)
    : [];

  return (
    <div className="notification-bell-wrap" ref={panelRef}>
      <button
        type="button"
        className="notification-bell-btn"
        aria-label={`Notifications${count ? `, ${count} active` : ""}`}
        onClick={() => {
          setOpen((v) => !v);
          if (!open) void load();
        }}
      >
        <IconBell />
        {count > 0 && <span className="notification-bell-badge">{count > 99 ? "99+" : count}</span>}
      </button>

      {open && (
        <div className="notification-bell-panel">
          <div className="notification-bell-panel-header">
            <strong>Notifications</strong>
            <Link to="/notifications" className="notification-bell-view-all" onClick={() => setOpen(false)}>
              View all
            </Link>
          </div>
          {loading && !inbox && <p className="subtitle">Loading…</p>}
          {!loading && snippets.length === 0 && (
            <p className="subtitle" style={{ padding: "0.75rem 1rem" }}>
              No active alerts — bills, cards, and housing are scanned daily.
            </p>
          )}
          <ul className="notification-bell-list">
            {snippets.map((item) => (
              <li key={item.id} className="notification-bell-item">
                <div className="notification-bell-item-body">
                  <div className="notification-bell-item-title">{item.title}</div>
                  <div className="notification-bell-item-snippet">{item.body}</div>
                </div>
                <button
                  type="button"
                  className="notification-bell-dismiss"
                  aria-label="Dismiss"
                  title="Dismiss"
                  onClick={() => void dismiss(item)}
                >
                  <IconClose size={14} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
