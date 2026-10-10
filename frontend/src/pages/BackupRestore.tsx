import { useCallback, useEffect, useState } from "react";
import { api, type BackupRecord } from "../api";
import { Btn, Card, EmptyState, PageHeader, Toggle } from "../components/ui";

export default function BackupRestore() {
  const [backups, setBackups] = useState<BackupRecord[]>([]);
  const [includeEvidence, setIncludeEvidence] = useState(false);
  const [redactedExport, setRedactedExport] = useState(true);
  const [restoreId, setRestoreId] = useState<string | null>(null);
  const [restoreConfirm, setRestoreConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .listBackups()
      .then((r) => {
        setBackups(r.backups);
        setErr("");
      })
      .catch((e) => setErr(e instanceof Error ? e.message : "Could not load backups"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const createBackup = async () => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.createBackup(includeEvidence);
      setMsg(`Backup created: ${res.backup_id}`);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Backup failed");
    } finally {
      setBusy(false);
    }
  };

  const exportSummary = async () => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.exportBackupSummary(redactedExport, includeEvidence);
      setMsg(`Export ready: ${res.export_id}`);
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  };

  const restore = async (backupId: string) => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const res = await api.restoreBackup(backupId, restoreConfirm);
      setMsg(res.message || "Restore completed.");
      setRestoreId(null);
      setRestoreConfirm("");
      load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Restore failed");
    } finally {
      setBusy(false);
    }
  };

  const formatSize = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <>
      <PageHeader
        title="Backup & restore"
        subtitle="Protect household data with portable exports. Secrets and .env are never included."
      />

      {err && <p className="error">{err}</p>}
      {msg && <p style={{ color: "var(--safe)" }}>{msg}</p>}

      <Card>
        <h3 style={{ marginTop: 0 }}>Create backup</h3>
        <Toggle
          label="Include evidence files"
          checked={includeEvidence}
          onChange={setIncludeEvidence}
        />
        <div style={{ marginTop: "0.75rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
          <Btn onClick={createBackup} disabled={busy}>
            {busy ? "Working…" : "Create full backup"}
          </Btn>
        </div>
      </Card>

      <Card>
        <h3 style={{ marginTop: 0 }}>Portable export</h3>
        <Toggle
          label="Redacted export (hide API keys & secrets)"
          checked={redactedExport}
          onChange={setRedactedExport}
        />
        <div style={{ marginTop: "0.75rem" }}>
          <Btn variant="ghost" onClick={exportSummary} disabled={busy}>
            Generate export bundle
          </Btn>
        </div>
      </Card>

      <h3>Backups</h3>
      <div className="list-group">
        {backups.map((b) => (
          <div className="list-row" key={b.backup_id}>
            <div className="list-row-body">
              <div className="list-row-title">
                {b.backup_id}
                {b.pre_restore ? (
                  <span className="badge" style={{ marginLeft: 8 }}>
                    pre-restore
                  </span>
                ) : null}
              </div>
              <div className="list-row-sub">
                {b.created_at} · {formatSize(b.size_bytes)}
                {b.include_evidence ? " · includes evidence" : ""}
              </div>
            </div>
            <div style={{ display: "flex", gap: "0.35rem", flexWrap: "wrap", alignItems: "center" }}>
              <Btn
                variant="ghost"
                onClick={() =>
                  api.downloadBackupArtifact(
                    b.backup_id,
                    "zip",
                    `ledger-backup-${b.backup_id}.zip`
                  )
                }
              >
                ZIP
              </Btn>
              <Btn
                variant="ghost"
                onClick={() =>
                  api.downloadBackupArtifact(
                    b.backup_id,
                    "ledger",
                    `ledger-export-${b.backup_id}.json`
                  )
                }
              >
                JSON
              </Btn>
              <Btn
                variant="ghost"
                onClick={() =>
                  api.downloadBackupArtifact(
                    b.backup_id,
                    "summary",
                    `household-summary-${b.backup_id}.md`
                  )
                }
              >
                Summary
              </Btn>
              <Btn
                variant="ghost"
                onClick={() => {
                  setRestoreId(b.backup_id);
                  setRestoreConfirm("");
                }}
              >
                Restore
              </Btn>
            </div>
          </div>
        ))}
      </div>
      {!backups.length && !err && <EmptyState message="No backups yet. Create one above." />}

      {restoreId && (
        <Card className="account-reset-card">
          <h3 style={{ marginTop: 0 }}>Restore backup</h3>
          <p style={{ fontSize: "0.9rem", color: "var(--muted)", lineHeight: 1.5 }}>
            Restoring replaces your current household database. A pre-restore backup is created
            automatically. Type <strong>RESTORE</strong> to confirm.
          </p>
          <p style={{ fontSize: "0.85rem" }}>
            Backup: <code>{restoreId}</code>
          </p>
          <input
            className="input"
            value={restoreConfirm}
            onChange={(e) => setRestoreConfirm(e.target.value)}
            placeholder="RESTORE"
            aria-label="Confirm restore"
            style={{ maxWidth: 200, marginBottom: "0.5rem" }}
          />
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Btn
              onClick={() => restore(restoreId)}
              disabled={busy || restoreConfirm !== "RESTORE"}
            >
              {busy ? "Restoring…" : "Confirm restore"}
            </Btn>
            <Btn variant="ghost" onClick={() => setRestoreId(null)} disabled={busy}>
              Cancel
            </Btn>
          </div>
        </Card>
      )}
    </>
  );
}
