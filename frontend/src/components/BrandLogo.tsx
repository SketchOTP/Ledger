/** Ledger logo mark (icon + wordmark). */

import { assetUrl } from "../basePath";

const ICON_SRC = assetUrl("ledger-icon-192.png");

type BrandLogoProps = {
  size?: "sm" | "md";
  className?: string;
};

export function BrandLogo({ size = "md", className = "" }: BrandLogoProps) {
  const dim = size === "sm" ? 28 : 36;
  return (
    <div className={`brand-mark brand-mark--${size} ${className}`.trim()} aria-label="Ledger">
      <img src={ICON_SRC} alt="" width={dim} height={dim} className="brand-mark-icon" />
      <span className="brand-mark-text">Ledger</span>
    </div>
  );
}
