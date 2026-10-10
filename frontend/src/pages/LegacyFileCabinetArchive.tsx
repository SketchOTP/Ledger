import { useCallback, useEffect, useState } from "react";
import { api, type LegacyFileCabinetStatus } from "../api";
import { Btn, Card, EmptyState, PageHeader } from "../components/ui";

export default function LegacyFileCabinetArchive() {
  const [status, setStatus] = useState<LegacyFileCabinetStatus | null>(null);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);

  const load = useCallback(() => {
    api.legacyFileCabinetStatus().then(setStatus).catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Could not load the legacy archive.");
    });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const exportArchive = async () => {
    setExporting(true);
    setError("");
    try {
      const blob = await api.exportLegacyFileCabinet();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "file-cabinet-archive.zip";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not export the legacy archive.");
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageHeader
        title="File Cabinet archive"
        subtitle="The standalone File Cabinet has been retired from Ledger V2. Existing records and files remain available for recovery."
      />
      {error ? <p className="error">{error}</p> : null}
      {!status ? <p className="subtitle">Loading archive status…</p> : null}
      {status && !status.has_data ? (
        <EmptyState message="No File Cabinet records or files were found for this household." />
      ) : null}
      {status?.has_data ? (
        <Card>
          <h3 style={{ marginTop: 0 }}>Archived data</h3>
          <p className="subtitle">
            {status.summary.folder_count} folders · {status.summary.document_count} records · {status.summary.file_count} files · {status.summary.note_count} notes
          </p>
          {status.summary.missing_file_count || status.summary.unsafe_path_count || status.summary.orphan_file_count ? (
            <p className="subtitle">
              Integrity review: {status.summary.missing_file_count} missing, {status.summary.unsafe_path_count} unsafe-path, and {status.summary.orphan_file_count} orphan files. Export preserves detectable archive data without deleting or repairing it.
            </p>
          ) : null}
          <Btn onClick={exportArchive} disabled={exporting}>
            {exporting ? "Preparing export…" : "Export existing documents"}
          </Btn>
        </Card>
      ) : null}
    </>
  );
}
