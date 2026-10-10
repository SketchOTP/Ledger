/** Vite `base` (/lm/) → API prefix and React Router basename. */
export function routerBasename(): string | undefined {
  const base = import.meta.env.BASE_URL || "/";
  const trimmed = base.replace(/\/$/, "");
  return trimmed || undefined;
}

export function apiBasePath(): string {
  const base = import.meta.env.BASE_URL || "/";
  return `${base}api`.replace(/\/+/g, "/");
}

export function assetUrl(path: string): string {
  const base = import.meta.env.BASE_URL || "/";
  return `${base}${path.replace(/^\//, "")}`;
}
