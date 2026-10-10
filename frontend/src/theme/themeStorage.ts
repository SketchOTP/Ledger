export type ThemeMode = "light" | "dark";

const STORAGE_PREFIX = "lm_theme";

export function themeStorageKey(userId: string): string {
  return `${STORAGE_PREFIX}:${userId}`;
}

export function readStoredTheme(userId: string | null | undefined): ThemeMode {
  if (!userId) return "dark";
  const raw = localStorage.getItem(themeStorageKey(userId));
  return raw === "light" ? "light" : "dark";
}

export function writeStoredTheme(userId: string, theme: ThemeMode): void {
  localStorage.setItem(themeStorageKey(userId), theme);
}

export function applyThemeToDocument(theme: ThemeMode): void {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", theme === "dark" ? "#000000" : "#f2f2f7");
  }
}
