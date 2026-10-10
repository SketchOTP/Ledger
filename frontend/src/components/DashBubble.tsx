import { Link } from "react-router-dom";
import { Pill } from "./ui";

export interface DashBubbleProps {
  title: string;
  to: string;
  summary: string;
  detail?: string;
  tone?: "safe" | "tight" | "danger" | "neutral";
  wide?: boolean;
  children?: React.ReactNode;
}

export default function DashBubble({
  title,
  to,
  summary,
  detail,
  tone = "neutral",
  wide,
  children,
}: DashBubbleProps) {
  return (
    <Link to={to} className={`dash-bubble ${wide ? "dash-bubble-wide" : ""}`}>
      <div className="dash-bubble-head">
        <span className="dash-bubble-title">{title}</span>
        <span className="dash-bubble-chevron">›</span>
      </div>
      <p className="dash-bubble-summary">{summary}</p>
      {detail && <p className="dash-bubble-detail">{detail}</p>}
      {tone !== "neutral" && (
        <div className="dash-bubble-foot">
          <Pill tone={tone}>{tone === "safe" ? "OK" : tone === "tight" ? "Watch" : "Alert"}</Pill>
        </div>
      )}
      {children}
    </Link>
  );
}
