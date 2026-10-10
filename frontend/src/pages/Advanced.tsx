import { Link } from "react-router-dom";
import { Card, PageHeader } from "../components/ui";

const support = [
  ["Cash flow & forecast", "/cashflow"],
  ["Data quality", "/data-quality"],
  ["Backup & restore", "/backup"],
  ["Settings", "/settings"],
];

export default function Advanced() {
  return (
    <>
      <PageHeader title="Advanced" subtitle="Support and recovery tools for Ledger." />
      <div className="advanced-links">
        {support.map(([label, path]) => (
          <Card key={path}>
            <Link to={path}>{label}</Link>
          </Card>
        ))}
      </div>
    </>
  );
}
