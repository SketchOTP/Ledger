import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SVGAttributes } from "react";

const svgIcon = (size: number): SVGAttributes<SVGSVGElement> => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  "aria-hidden": true,
});

/** White stroke icons for use inside blue circle buttons (`color: #fff` on parent). */
export function IconPlus({ size = 18 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" />
    </svg>
  );
}

export function IconClose({ size = 16 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2.25" strokeLinecap="round" />
    </svg>
  );
}

export function IconPencil({ size = 16 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path
        d="M12 20h7M15.5 5.5l3 3-9 9H7v-2.5l9-9z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function IconCheckCircle({ size = 19 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
      <path d="m8 12.2 2.5 2.5L16.5 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function IconImport({ size = 18 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path d="M12 3v10m0 0l4-4m-4 4L8 9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function IconExport({ size = 18 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path d="M12 21V11m0 0l4 4m-4-4l-4 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 15v4a2 2 0 002 2h12a2 2 0 002-2v-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

/** Shown in light mode — tap to switch to dark mode. */
export function IconMoon({ size = 20 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <path
        d="M21 14.5A8.5 8.5 0 1 1 9.5 3a6.5 6.5 0 1 0 11.5 11.5z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Shown in dark mode — tap to switch to light mode. */
export function IconSun({ size = 20 }: { size?: number }) {
  return (
    <svg {...svgIcon(size)}>
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="2" />
      <path
        d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-header-row">
        <div className="page-header-titles">
          <h1>{title}</h1>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {action ? <div className="page-header-action">{action}</div> : null}
      </div>
    </header>
  );
}

export function Card({
  children,
  className = "",
  onClick,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      type={onClick ? "button" : undefined}
      className={`card ${onClick ? "card-tappable" : ""} ${className}`.trim()}
      onClick={onClick}
    >
      {children}
    </Tag>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="tablist">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          role="tab"
          aria-selected={value === opt.value}
          className={value === opt.value ? "active" : ""}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function Btn({
  children,
  variant = "primary",
  full,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  full?: boolean;
}) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} ${full ? "btn-full" : ""}`}
      {...props}
    >
      {children}
    </button>
  );
}

/** Blue circle + white icon (add / close / etc.). */
export function IconBtn({
  children,
  className = "",
  title,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  title: string;
}) {
  return (
    <button
      type={type}
      className={`circle-action-btn ${className}`.trim()}
      title={title}
      aria-label={title}
      {...props}
    >
      {children}
    </button>
  );
}

export function CsvImportButton({ onFile }: { onFile: (file: File) => void }) {
  return (
    <label
      className="circle-action-btn csv-upload-label"
      title="Import CSV"
      aria-label="Import CSV"
    >
      <IconImport />
      <input
        type="file"
        accept=".csv,text/csv"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </label>
  );
}

export function CsvExportButton({ onClick }: { onClick: () => void }) {
  return (
    <IconBtn type="button" title="Export CSV" onClick={onClick}>
      <IconExport />
    </IconBtn>
  );
}

export function BtnSubmit({
  children,
  full,
  disabled,
}: {
  children: ReactNode;
  full?: boolean;
  disabled?: boolean;
}) {
  return (
    <button type="submit" className={`btn btn-primary ${full ? "btn-full" : ""}`} disabled={disabled}>
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className="input" {...props} />;
}

export function ListRow({
  title,
  subtitle,
  trailing,
  trailTone = "default",
  onEdit,
  onDelete,
  onMarkPaid,
  markPaidLabel = "$",
  onMarkReceived,
  markReceivedLabel = "Received",
}: {
  title: string;
  subtitle?: string;
  trailing?: string;
  trailTone?: "default" | "danger" | "safe";
  onEdit?: () => void;
  onDelete?: () => void;
  onMarkPaid?: () => void;
  markPaidLabel?: string;
  onMarkReceived?: () => void;
  markReceivedLabel?: string;
}) {
  return (
    <div className="list-row">
      <div className="list-row-body">
        <div className="list-row-title">{title}</div>
        {subtitle && <div className="list-row-sub">{subtitle}</div>}
      </div>
      {trailing && (
        <div
          className={`list-row-trail${trailTone === "danger" ? " danger" : ""}${trailTone === "safe" ? " safe" : ""}`}
        >
          {trailing}
        </div>
      )}
      {onMarkReceived && (
        <button
          type="button"
          className="list-row-mark-received"
          onClick={onMarkReceived}
          aria-label="Mark received"
          title="Mark received"
        >
          {markReceivedLabel}
        </button>
      )}
      {onMarkPaid && (
        <button
          type="button"
          className="list-row-mark-paid"
          onClick={onMarkPaid}
          aria-label="Mark paid"
          title="Mark paid"
        >
          {markPaidLabel}
        </button>
      )}
      {onEdit && (
        <button type="button" className="circle-action-btn" onClick={onEdit} aria-label="Edit" title="Edit">
          <IconPencil size={15} />
        </button>
      )}
      {onDelete && (
        <button type="button" className="list-row-delete" onClick={onDelete} aria-label="Remove">
          ×
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <p>{message}</p>
      {action}
    </div>
  );
}

export function HeroStat({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: string;
  tone?: "default" | "safe" | "tight" | "danger";
}) {
  return (
    <div className={`hero-stat tone-${tone}`}>
      <div className="hero-stat-label">{label}</div>
      <div className="hero-stat-value">{value}</div>
    </div>
  );
}

export function Pill({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

export function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
}) {
  return (
    <label className="toggle-row">
      <span className="toggle-text">
        <span className="toggle-label">{label}</span>
        {hint && <span className="field-hint">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        className={`toggle ${checked ? "on" : ""}`}
        onClick={() => onChange(!checked)}
      >
        <span className="toggle-knob" />
      </button>
    </label>
  );
}
