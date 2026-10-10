import { lazy, Suspense } from "react";
import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { ThemeProvider } from "./theme/ThemeContext";
import Login from "./pages/Login";
import Register from "./pages/Register";
import { BrandLogo } from "./components/BrandLogo";

const Money = lazy(() => import("./pages/MyMoney"));
const Today = lazy(() => import("./pages/Today"));
const Advanced = lazy(() => import("./pages/Advanced"));
const CashFlow = lazy(() => import("./pages/CashFlow"));
const Forecast = lazy(() => import("./pages/Forecast"));
const DataQuality = lazy(() => import("./pages/DataQuality"));
const Settings = lazy(() => import("./pages/Settings"));
const LegacyFileCabinetArchive = lazy(() => import("./pages/LegacyFileCabinetArchive"));
const BackupRestore = lazy(() => import("./pages/BackupRestore"));
const Notifications = lazy(() => import("./pages/Notifications"));
const Onboarding = lazy(() => import("./pages/Onboarding"));
const Transactions = lazy(() => import("./pages/Transactions"));
const Household = lazy(() => import("./pages/Household"));

function RouteLoading() {
  return <p className="subtitle">Loading…</p>;
}

const tabs = [
  { to: "/", label: "Today", icon: "⌂", end: true },
  { to: "/money", label: "Money", icon: "◉", end: false },
  { to: "/advanced", label: "Advanced", icon: "⋯", end: false },
];

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <RouteLoading />;
  if (!token) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return <>{children}</>;
}

function RequireOnboardingComplete({ children }: { children: React.ReactNode }) {
  const { onboardingRequired, loading } = useAuth();
  if (loading) return <RouteLoading />;
  if (onboardingRequired) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function AppShell() {
  return (
    <div className="layout">
      <aside className="side-nav" aria-label="Main navigation">
        <BrandLogo className="side-nav-brand" />
        <p className="side-nav-tagline">Household CFO</p>
        <nav className="side-nav-links">
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end} className="side-nav-link">
              <span className="nav-icon">{t.icon}</span>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="layout-body">
        <main className="main">
          <Suspense fallback={<RouteLoading />}>
            <Outlet />
          </Suspense>
        </main>

        <nav className="bottom-nav mobile-only" aria-label="Main">
          {tabs.map((t) => (
            <NavLink key={t.to} to={t.to} end={t.end}>
              <span className="nav-icon">{t.icon}</span>
              {t.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <ThemeProvider>
        <Suspense fallback={<RouteLoading />}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />

            <Route
              element={
                <RequireAuth>
                  <Outlet />
                </RequireAuth>
              }
            >
              <Route path="/onboarding" element={<Onboarding />} />

              <Route
                element={
                  <RequireOnboardingComplete>
                    <AppShell />
                  </RequireOnboardingComplete>
                }
              >
                <Route path="/" element={<Today />} />
                <Route path="/home" element={<Navigate to="/" replace />} />
                <Route path="/household-setup" element={<Navigate to="/money" replace />} />
                <Route path="/daily-briefing" element={<Navigate to="/" replace />} />
                <Route path="/notifications" element={<Notifications />} />
                <Route path="/money" element={<Money />} />
                <Route path="/moves" element={<Navigate to="/" replace />} />
                <Route path="/household" element={<Household />} />
                <Route path="/advanced" element={<Advanced />} />
                <Route path="/more" element={<Navigate to="/advanced" replace />} />
                <Route path="/overview" element={<Navigate to="/" replace />} />
                <Route path="/cashflow" element={<CashFlow />} />
                <Route path="/bills" element={<Navigate to="/money?section=bills" replace />} />
                <Route path="/debts" element={<Navigate to="/money?section=debts" replace />} />
                <Route path="/mortgage" element={<Navigate to="/money?section=mortgage" replace />} />
                <Route path="/credit-cards" element={<Navigate to="/money?section=credit_cards" replace />} />
                <Route path="/offers" element={<Navigate to="/" replace />} />
                <Route path="/groceries" element={<Navigate to="/money" replace />} />
                <Route path="/grocery-deals" element={<Navigate to="/money" replace />} />
                <Route path="/grocery-optimizer" element={<Navigate to="/money" replace />} />
                <Route path="/evidence" element={<Navigate to="/advanced" replace />} />
                <Route path="/evidence-review" element={<Navigate to="/advanced" replace />} />
                <Route path="/reports" element={<Navigate to="/advanced" replace />} />
                <Route path="/opportunities" element={<Navigate to="/" replace />} />
                <Route path="/simulations" element={<Navigate to="/" replace />} />
                <Route path="/forecast" element={<Forecast />} />
                <Route path="/data-quality" element={<DataQuality />} />
                <Route path="/outcome-learning" element={<Navigate to="/" replace />} />
                <Route path="/financial-timeline" element={<Navigate to="/advanced" replace />} />
                <Route path="/financial-memory" element={<Navigate to="/financial-timeline" replace />} />
                <Route path="/recommendations" element={<Navigate to="/" replace />} />
                <Route path="/actions" element={<Navigate to="/" replace />} />
                <Route path="/events" element={<Navigate to="/advanced" replace />} />
                <Route path="/plugins" element={<Navigate to="/advanced" replace />} />
                <Route path="/research-admin" element={<Navigate to="/advanced" replace />} />
                <Route path="/scheduler" element={<Navigate to="/advanced" replace />} />
                <Route path="/offer-intake" element={<Navigate to="/" replace />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/ai-settings" element={<Navigate to="/settings" replace />} />
                <Route path="/file-cabinet" element={<LegacyFileCabinetArchive />} />
                <Route path="/data-fixes" element={<Navigate to="/data-quality" replace />} />
                <Route path="/backup" element={<BackupRestore />} />
                <Route path="/behavioral-finance" element={<Navigate to="/" replace />} />
                <Route path="/transactions" element={<Transactions />} />
              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </ThemeProvider>
    </AuthProvider>
  );
}
