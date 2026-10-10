import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, type AuthUser } from "../api";
import { clearAuthToken, readAuthToken, storeAuthToken } from "./tokenStorage";

type AuthState = {
  user: AuthUser | null;
  token: string | null;
  loading: boolean;
  onboardingRequired: boolean;
  login: (username: string, password: string, rememberMe?: boolean) => Promise<boolean>;
  register: (
    username: string,
    password: string,
    displayName?: string,
    rememberMe?: boolean
  ) => Promise<void>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(() => readAuthToken());
  const [loading, setLoading] = useState(true);
  const [onboardingRequired, setOnboardingRequired] = useState(false);

  const refreshMe = useCallback(async () => {
    if (!readAuthToken()) {
      setUser(null);
      setOnboardingRequired(false);
      setLoading(false);
      return;
    }
    try {
      const me = await api.me();
      setUser({ id: me.id, username: me.username, display_name: me.display_name });
      setOnboardingRequired(me.onboarding_required);
    } catch {
      clearAuthToken();
      setToken(null);
      setUser(null);
      setOnboardingRequired(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshMe();
  }, [refreshMe, token]);

  const login = useCallback(async (username: string, password: string, rememberMe = true) => {
    const res = await api.login(username, password, rememberMe);
    storeAuthToken(res.token, rememberMe);
    setToken(res.token);
    setUser(res.user);
    setOnboardingRequired(res.onboarding_required);
    return res.onboarding_required;
  }, []);

  const register = useCallback(
    async (username: string, password: string, displayName?: string, rememberMe = true) => {
      const res = await api.register(username, password, displayName, rememberMe);
      storeAuthToken(res.token, rememberMe);
      setToken(res.token);
      setUser(res.user);
      setOnboardingRequired(true);
    },
    []
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    clearAuthToken();
    setToken(null);
    setUser(null);
    setOnboardingRequired(false);
  }, []);

  const value = useMemo(
    () => ({
      user,
      token,
      loading,
      onboardingRequired,
      login,
      register,
      logout,
      refreshMe,
    }),
    [user, token, loading, onboardingRequired, login, register, logout, refreshMe]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
