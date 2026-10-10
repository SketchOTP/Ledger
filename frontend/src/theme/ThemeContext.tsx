import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "../auth/AuthContext";
import {
  applyThemeToDocument,
  readStoredTheme,
  writeStoredTheme,
  type ThemeMode,
} from "./themeStorage";

type ThemeContextValue = {
  theme: ThemeMode;
  toggleTheme: () => void;
  setTheme: (theme: ThemeMode) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [theme, setThemeState] = useState<ThemeMode>("dark");

  useEffect(() => {
    const next = user?.id ? readStoredTheme(user.id) : "dark";
    setThemeState(next);
    applyThemeToDocument(next);
  }, [user?.id]);

  const persistTheme = useCallback(
    (next: ThemeMode) => {
      setThemeState(next);
      applyThemeToDocument(next);
      if (user?.id) {
        writeStoredTheme(user.id, next);
      }
    },
    [user?.id]
  );

  const setTheme = useCallback(
    (next: ThemeMode) => {
      persistTheme(next);
    },
    [persistTheme]
  );

  const toggleTheme = useCallback(() => {
    setThemeState((current) => {
      const next: ThemeMode = current === "dark" ? "light" : "dark";
      applyThemeToDocument(next);
      if (user?.id) {
        writeStoredTheme(user.id, next);
      }
      return next;
    });
  }, [user?.id]);

  const value = useMemo(
    () => ({ theme, toggleTheme, setTheme }),
    [theme, toggleTheme, setTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
