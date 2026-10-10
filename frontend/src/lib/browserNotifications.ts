/** Browser / PWA notification helpers — permission must come from a user tap. */

import { api } from "../api";
import { assetUrl, routerBasename } from "../basePath";

const NOTIFIED_KEY = "lm_notified_ids";
const ENABLED_KEY = "lm_notifications_enabled";

function appPath(path: string): string {
  const base = routerBasename() || "";
  const segment = path.startsWith("/") ? path : `/${path}`;
  return `${base}${segment}`.replace(/\/+/g, "/") || "/";
}

export type NotificationSetupState =
  | "unsupported"
  | "denied"
  | "default"
  | "granted"
  | "granted_no_sw"
  | "granted_no_push";

function readNotifiedIds(): Set<string> {
  try {
    const raw = localStorage.getItem(NOTIFIED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as string[];
    return new Set(Array.isArray(parsed) ? parsed : []);
  } catch {
    return new Set();
  }
}

function writeNotifiedIds(ids: Set<string>): void {
  const trimmed = [...ids].slice(-200);
  localStorage.setItem(NOTIFIED_KEY, JSON.stringify(trimmed));
}

export function notificationsUserEnabled(): boolean {
  return localStorage.getItem(ENABLED_KEY) === "1";
}

export function pushSupported(): boolean {
  return (
    "Notification" in window &&
    "serviceWorker" in navigator &&
    "PushManager" in window
  );
}

export function getNotificationSetupState(): NotificationSetupState {
  if (!("Notification" in window)) return "unsupported";
  const perm = Notification.permission;
  if (perm === "denied") return "denied";
  if (perm === "default") return "default";
  if (!("serviceWorker" in navigator)) return "granted_no_sw";
  if (!("PushManager" in window)) return "granted_no_push";
  return "granted";
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register(assetUrl("sw.js"));
  } catch {
    return null;
  }
}

async function subscribeWebPush(reg: ServiceWorkerRegistration): Promise<boolean> {
  if (!("PushManager" in window)) return false;
  try {
    const { public_key } = await api.pushPublicKey();
    const existing = await reg.pushManager.getSubscription();
    const sub =
      existing ??
      (await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(public_key) as BufferSource,
      }));
    const json = sub.toJSON();
    if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) return false;
    await api.pushSubscribe({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
      expirationTime: json.expirationTime ?? null,
    });
    return true;
  } catch {
    return false;
  }
}

/** Call only from a button click — registers SW then requests permission. */
export async function enableBrowserNotifications(): Promise<NotificationSetupState> {
  if (!("Notification" in window)) return "unsupported";

  const reg = await registerServiceWorker();

  let perm = Notification.permission;
  if (perm === "default") {
    perm = await Notification.requestPermission();
  }

  if (perm === "granted") {
    localStorage.setItem(ENABLED_KEY, "1");
    if (!reg) return "granted_no_sw";
    if (!("PushManager" in window)) return "granted_no_push";
    const subscribed = await subscribeWebPush(reg);
    return subscribed ? "granted" : "granted_no_push";
  }
  if (perm === "denied") return "denied";
  return "default";
}

export async function sendTestNotification(): Promise<void> {
  try {
    await api.pushTest();
    return;
  } catch {
    /* fall back to local notification when push not configured */
  }
  await showBrowserNotification("Ledger notifications enabled", {
    body: "You will get alerts when bills are due or cashflow needs attention.",
    tag: "lm-test-notification",
    url: "/notifications",
  });
}

export async function showBrowserNotification(
  title: string,
  options?: { body?: string; tag?: string; url?: string }
): Promise<void> {
  if (!("Notification" in window) || Notification.permission !== "granted") return;

  const body = options?.body ?? "";
  const tag = options?.tag ?? title;
  const url = appPath(options?.url ?? "/notifications");
  const icon = assetUrl("ledger-icon-192.png");

  if ("serviceWorker" in navigator) {
    const reg = await navigator.serviceWorker.ready.catch(() => null);
    if (reg) {
      await reg.showNotification(title, {
        body,
        tag,
        icon,
        badge: icon,
        data: { url },
      });
      return;
    }
  }

  const n = new Notification(title, { body, tag, icon });
  n.onclick = () => {
    window.focus();
    window.location.href = url;
  };
}

export async function notifyNewItems(
  items: { id: string; title: string; body: string; link_path?: string }[]
): Promise<void> {
  if (!items.length) return;
  if (!notificationsUserEnabled()) return;
  if (Notification.permission !== "granted") return;

  const seen = readNotifiedIds();
  const fresh = items.filter((i) => !seen.has(i.id));
  if (!fresh.length) return;

  for (const item of fresh.slice(0, 5)) {
    await showBrowserNotification(item.title, {
      body: item.body,
      tag: item.id,
      url: item.link_path || "/notifications",
    });
    seen.add(item.id);
  }
  writeNotifiedIds(seen);
}

export function clearNotifiedId(id: string): void {
  const seen = readNotifiedIds();
  seen.delete(id);
  writeNotifiedIds(seen);
}
