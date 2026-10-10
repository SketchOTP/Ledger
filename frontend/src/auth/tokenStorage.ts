/** Local-only auth token storage — remember me uses persistent localStorage. */

export const TOKEN_KEY = "lm_token";
const REMEMBER_PREF_KEY = "lm_remember";

export function readAuthToken(): string | null {
  return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY);
}

export function rememberMeEnabled(): boolean {
  return localStorage.getItem(REMEMBER_PREF_KEY) !== "0";
}

export function storeAuthToken(token: string, rememberMe: boolean): void {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
  const storage = rememberMe ? localStorage : sessionStorage;
  storage.setItem(TOKEN_KEY, token);
  localStorage.setItem(REMEMBER_PREF_KEY, rememberMe ? "1" : "0");
}

export function clearAuthToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REMEMBER_PREF_KEY);
}
