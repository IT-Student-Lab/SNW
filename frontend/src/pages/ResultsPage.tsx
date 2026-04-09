import { useEffect, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import api from "../api";
import "./ResultsPage.css";

interface FileInfo {
  filename: string;
  size_bytes: number;
  extension: string;
}

interface QsImage {
  filename: string;
  caption: string;
}

interface WikiImage {
  title: string;
  thumb_url: string;
  description_url: string;
}

interface LocationInfo {
  title: "_location_info";
  display_name: string;
  gemeente: string;
  provincie: string;
  waterschap: string;
  woonplaats: string;
  buurt: string;
  radius: number;
}

interface AnalysisSection {
  title: string;
  images: QsImage[];
  analysis: string;
  kadaster_parcel?: Record<string, unknown> | null;
  omgevingsvisie?: string | null;
  history_web?: string | null;
  wikimedia_images?: WikiImage[] | null;
}

type QuickscanSection = LocationInfo | AnalysisSection;

function isLocationInfo(s: QuickscanSection): s is LocationInfo {
  return s.title === "_location_info";
}

export default function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const locState = location.state as {
    address?: string;
    quickscanSections?: QuickscanSection[];
  } | null;
  const address = locState?.address ?? "";

  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);

  // Quickscan — may arrive via navigation state from generator SSE
  const [qsLoading, setQsLoading] = useState(false);
  const [qsSections, setQsSections] = useState<QuickscanSection[] | null>(
    locState?.quickscanSections ?? null
  );
  const [qsError, setQsError] = useState("");

  // PPTX export
  const [pptxLoading, setPptxLoading] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    api
      .get(`/api/files/${jobId}`)
      .then((r) => setFiles(r.data.files))
      .catch(() => setFiles([]))
      .finally(() => setLoadingFiles(false));
  }, [jobId]);

  /** Download via axios (includes JWT) then trigger browser save */
  const downloadFile = async (filename: string) => {
    const res = await api.get(
      `/api/files/${jobId}/download/${filename}`,
      { responseType: "blob" }
    );
    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadZip = async () => {
    const res = await api.get(`/api/files/${jobId}/zip`, {
      responseType: "blob",
    });
    const url = URL.createObjectURL(res.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${jobId}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const runQuickscan = async () => {
    setQsLoading(true);
    setQsError("");
    try {
      const res = await api.post(
        `/api/quickscan/${jobId}`,
        null,
        { params: { address, radius: 250 } }
      );
      setQsSections(res.data.sections);
    } catch (err: unknown) {
      const detail =
        err instanceof Error ? err.message : "Quickscan mislukt.";
      setQsError(detail);
    } finally {
      setQsLoading(false);
    }
  };

  const fmtSize = (b: number) =>
    b > 1_000_000
      ? `${(b / 1_000_000).toFixed(1)} MB`
      : `${(b / 1_000).toFixed(0)} KB`;

  const exportPptx = async () => {
    setPptxLoading(true);
    try {
      const res = await api.post(
        `/api/quickscan/${jobId}/export`,
        null,
        {
          params: { address, radius: 250 },
          responseType: "blob",
        }
      );
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `quickscan_${jobId}.pptx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      alert("PPTX export mislukt.");
    } finally {
      setPptxLoading(false);
    }
  };

  /** Build an authenticated image URL via object URL */
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});

  const loadImage = async (filename: string) => {
    if (imageUrls[filename]) return;
    try {
      const res = await api.get(
        `/api/files/${jobId}/download/${filename}`,
        { responseType: "blob" }
      );
      const url = URL.createObjectURL(res.data);
      setImageUrls((prev) => ({ ...prev, [filename]: url }));
    } catch {
      // silently skip broken images
    }
  };

  // Load images when quickscan sections arrive
  useEffect(() => {
    if (!qsSections) return;
    for (const s of qsSections) {
      if (isLocationInfo(s)) continue;
      for (const img of s.images) {
        loadImage(img.filename);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qsSections]);

  const renderLocationInfo = (s: LocationInfo) => (
    <article className="qs-card card qs-location-info">
      <h3>Locatie-informatie</h3>
      <table className="qs-info-table">
        <tbody>
          <tr><td>Adres</td><td>{s.display_name}</td></tr>
          <tr><td>Gemeente</td><td>{s.gemeente}</td></tr>
          <tr><td>Provincie</td><td>{s.provincie}</td></tr>
          <tr><td>Woonplaats</td><td>{s.woonplaats}</td></tr>
          <tr><td>Waterschap</td><td>{s.waterschap}</td></tr>
          <tr><td>Buurt</td><td>{s.buurt}</td></tr>
          <tr><td>Straal</td><td>{s.radius} m</td></tr>
        </tbody>
      </table>
    </article>
  );

  const renderAnalysisSection = (s: AnalysisSection) => (
    <article className="qs-card card">
      <h3>{s.title}</h3>

      {/* Section images */}
      {s.images.length > 0 && (
        <div className="qs-images">
          {s.images.map((img, j) => (
            <figure key={j}>
              {imageUrls[img.filename] ? (
                <img src={imageUrls[img.filename]} alt={img.caption} />
              ) : (
                <div className="qs-img-placeholder">
                  <div className="spinner" style={{ width: 20, height: 20 }} />
                </div>
              )}
              <figcaption>{img.caption}</figcaption>
            </figure>
          ))}
        </div>
      )}

      {/* AI analysis text */}
      {s.analysis && (
        <div className="qs-analysis">{s.analysis}</div>
      )}

      {/* Kadaster parcel info */}
      {s.kadaster_parcel && (
        <details className="qs-extra">
          <summary>Kadastrale perceelinformatie</summary>
          <pre>{JSON.stringify(s.kadaster_parcel, null, 2)}</pre>
        </details>
      )}

      {/* Omgevingsvisie */}
      {s.omgevingsvisie && (
        <div className="qs-omgevingsvisie">
          <h4>Omgevingsvisie</h4>
          <div className="qs-analysis">{s.omgevingsvisie}</div>
        </div>
      )}

      {/* History: web search results */}
      {s.history_web && (
        <div className="qs-history-web">
          <h4>Historische informatie</h4>
          <div className="qs-analysis">{s.history_web}</div>
        </div>
      )}

      {/* History: wikimedia images */}
      {s.wikimedia_images && s.wikimedia_images.length > 0 && (
        <div className="qs-wikimedia">
          <h4>Wikimedia Commons</h4>
          <div className="qs-images">
            {s.wikimedia_images.map((w, j) => (
              <figure key={j}>
                <a href={w.description_url} target="_blank" rel="noreferrer">
                  <img src={w.thumb_url} alt={w.title} />
                </a>
                <figcaption>{w.title}</figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}
    </article>
  );

  return (
    <div className="results-page">
      <h1>Resultaten</h1>
      {address && <p className="results-address">{address}</p>}

      {/* Files section */}
      <section className="card files-section">
        <h2>Bestanden</h2>
        {loadingFiles ? (
          <div className="spinner" />
        ) : files.length === 0 ? (
          <p>Geen bestanden gevonden.</p>
        ) : (
          <>
            <ul className="file-list">
              {files.map((f) => (
                <li key={f.filename}>
                  <span className="file-name">{f.filename}</span>
                  <span className="file-size">{fmtSize(f.size_bytes)}</span>
                  <button
                    className="btn btn-outline btn-sm"
                    onClick={() => downloadFile(f.filename)}
                  >
                    Download
                  </button>
                </li>
              ))}
            </ul>
            <button className="btn btn-secondary" onClick={downloadZip}>
              Download alles (ZIP)
            </button>
          </>
        )}
      </section>

      {/* Quickscan section */}
      <section className="card quickscan-section">
        <h2>AI Quickscan</h2>
        {qsSections === null && !qsLoading && (
          <button className="btn btn-primary" onClick={runQuickscan}>
            Start Quickscan
          </button>
        )}

        {qsLoading && (
          <div className="qs-loading">
            <div className="spinner" />
            <span>AI analyse bezig…</span>
          </div>
        )}

        {qsError && <div className="error-msg">{qsError}</div>}

        {qsSections && (
          <div className="qs-results">
            {qsSections.map((s, i) =>
              isLocationInfo(s) ? (
                <div key={i}>{renderLocationInfo(s)}</div>
              ) : (
                <div key={i}>{renderAnalysisSection(s)}</div>
              )
            )}

            <button
              className="btn btn-secondary"
              onClick={exportPptx}
              disabled={pptxLoading}
              style={{ marginTop: "var(--space-md)" }}
            >
              {pptxLoading ? "Exporteren…" : "Exporteer als PowerPoint (.pptx)"}
            </button>
          </div>
        )}
      </section>

      <button
        className="btn btn-outline"
        style={{ marginTop: "var(--space-lg)" }}
        onClick={() => navigate("/")}
      >
        ← Nieuwe generatie
      </button>
    </div>
  );
}
