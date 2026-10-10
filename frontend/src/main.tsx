import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { assetUrl, routerBasename } from "./basePath";
import "./styles.css";

/** ?fresh=1 — unregister service workers and strip param (fixes stale PWA / SW on phone). */
async function clearStaleClientCache(): Promise<boolean> {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("fresh")) return false;
  if ("serviceWorker" in navigator) {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister()));
  }
  if ("caches" in window) {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));
  }
  params.delete("fresh");
  const qs = params.toString();
  const next = `${window.location.pathname}${qs ? `?${qs}` : ""}${window.location.hash}`;
  window.location.replace(next);
  return true;
}

void clearStaleClientCache().then((redirecting) => {
  if (!redirecting) boot();
});

function boot() {
  ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename={routerBasename()}>
      <App />
    </BrowserRouter>
  </React.StrictMode>
  );

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register(assetUrl("sw.js")).catch(() => {
        /* non-fatal — app still works in browser tab */
      });
    });
  }
}
