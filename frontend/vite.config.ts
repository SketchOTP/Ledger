import path from "node:path";
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const rootEnv = loadEnv(mode, path.resolve(__dirname, ".."), "");
  // Production builds are launched by systemd with an external EnvironmentFile
  // rather than a checkout-local .env. Merge the process environment so the
  // generated asset URLs match the mounted public base (for example /lm/).
  const env = { ...rootEnv, ...process.env };
  const apiPort = env.API_PORT || "9543";
  const frontendPort = Number(env.FRONTEND_PORT || "9544");
  // Standalone installs use /; reverse proxies may set a mounted base.
  const publicBase = (env.LEDGERMIND_PUBLIC_BASE || "/").trim();
  const base =
    publicBase === "/" || publicBase === ""
      ? "/"
      : publicBase.endsWith("/")
        ? publicBase
        : `${publicBase}/`;
  const extraHosts = (env.VITE_ALLOWED_HOSTS || "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);
  const allowedHosts = ["localhost", "127.0.0.1", ...extraHosts];
  const apiTarget = `http://127.0.0.1:${apiPort}`;
  const apiProxy = { target: apiTarget, changeOrigin: true };
  const normalizedBase =
    publicBase === "/" || publicBase === "" ? "" : publicBase.replace(/\/$/, "");
  const proxy: Record<string, typeof apiProxy> = {
    "/api": apiProxy,
  };
  if (normalizedBase) {
    proxy[`${normalizedBase}/api`] = apiProxy;
  }

  return {
    base,
    plugins: [react()],
    server: {
      port: frontendPort,
      strictPort: true,
      allowedHosts,
      proxy,
    },
    preview: {
      port: frontendPort,
      strictPort: true,
      allowedHosts,
      proxy,
    },
    build: {
      outDir: "dist",
    },
  };
});
