import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type DataFixQueue, type QualityReport } from "../api";

const statusClass: Record<string, string> = {
  trusted: "risk-safe",
  usable: "risk-tight",
  weak: "risk-tight",
  unsafe: "risk-danger",
};

export default function DataQuality() {
  const [report, setReport] = useState<QualityReport | null>(null);
  const [queue, setQueue] = useState<DataFixQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([api.dataQuality(), api.dataFixes()])
      .then(([r, fixes]) => {
        setReport(r);
        setQueue(fixes);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <h2>Fix my data</h2>
      <p style={{ color: "var(--muted)", marginBottom: "1rem" }}>
        Trust layer for advice. Unsafe data blocks rate-sensitive suggestions; usable data allows
        advice with warnings.
      </p>

      <div className="form-row">
        <button type="button" onClick={load} disabled={loading}>
          {loading ? "Checking…" : "Re-check"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {report && (
        <>
          <div className="grid">
            <div className="card stat">
              <div className="label">Overall score</div>
              <div className={`value ${statusClass[report.status] ?? ""}`}>
                {report.overall_score}
              </div>
            </div>
            <div className="card stat">
              <div className="label">Status</div>
              <div className={`value ${statusClass[report.status] ?? ""}`}>
                {report.status}
              </div>
            </div>
            <div className="card stat">
              <div className="label">Blocking issues</div>
              <div className="value risk-danger">{report.blocking_issues.length}</div>
            </div>
            <div className="card stat">
              <div className="label">Warnings</div>
              <div className="value">{report.warnings.length}</div>
            </div>
          </div>

          {report.blocking_issues.length > 0 && (
            <div className="card">
              <h3>Blocking issues</h3>
              <ul>
                {report.blocking_issues.map((issue, i) => (
                  <li key={`b-${i}`}>
                    <span className="badge">{issue.domain}</span> {issue.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {report.recommended_fixes.length > 0 && (
            <div className="card">
              <h3>Recommended fixes</h3>
              <ul>
                {report.recommended_fixes.map((fix, i) => (
                  <li key={`f-${i}`}>{fix}</li>
                ))}
              </ul>
            </div>
          )}

          {queue && queue.items.length > 0 && (
            <div className="card">
              <h3>Actionable fixes</h3>
              <div className="data-fix-list">
                {queue.items.map((item) => (
                  <div key={item.id} className="data-fix-item">
                    <div className="data-fix-head">
                      <strong>{item.category}</strong>
                      <span className={`badge ${item.severity === "blocking" ? "risk-danger" : "risk-tight"}`}>
                        {item.severity}
                      </span>
                    </div>
                    <p style={{ margin: "0.35rem 0", fontSize: "0.9rem" }}>{item.message}</p>
                    <p className="field-hint" style={{ margin: "0 0 0.75rem" }}>{item.fix_hint}</p>
                    <Link to={item.fix_path} className="btn btn-primary btn-sm">Fix now</Link>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card">
            <h3>Domains</h3>
            <table>
              <thead>
                <tr>
                  <th>Domain</th>
                  <th>Score</th>
                  <th>Status</th>
                  <th>Issues</th>
                </tr>
              </thead>
              <tbody>
                {report.domains.map((d) => (
                  <tr key={d.domain}>
                    <td>{d.domain}</td>
                    <td>{d.score}</td>
                    <td>
                      <span className={`badge ${statusClass[d.status] ?? ""}`}>{d.status}</span>
                    </td>
                    <td>
                      {d.issues.length === 0 ? (
                        <span style={{ color: "var(--muted)" }}>—</span>
                      ) : (
                        <ul style={{ margin: 0, paddingLeft: "1.1rem", fontSize: "0.85rem" }}>
                          {d.issues.map((issue, i) => (
                            <li key={i}>
                              <code>{issue.check}</code>: {issue.message}
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p style={{ fontSize: "0.8rem", color: "var(--muted)" }}>
            Last checked: {report.checked_at}
          </p>
        </>
      )}
    </>
  );
}
