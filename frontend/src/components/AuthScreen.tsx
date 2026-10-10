import type { ReactNode } from "react";
import { BrandLogo } from "./BrandLogo";

export function AuthScreen({
  title,
  subtitle,
  wide,
  children,
}: {
  title: string;
  subtitle?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="auth-screen">
      <div className={`auth-panel ${wide ? "auth-panel--wide" : ""}`.trim()}>
        <header className="auth-panel-header">
          <BrandLogo size="sm" className="auth-panel-brand" />
          <h1 className="auth-panel-title">{title}</h1>
          {subtitle && <p className="auth-panel-subtitle">{subtitle}</p>}
        </header>
        {children}
      </div>
    </div>
  );
}
