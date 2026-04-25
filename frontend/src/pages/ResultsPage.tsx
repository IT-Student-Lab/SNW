import { useEffect, useState, ReactNode } from "react";
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
  local_file?: string;
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

/**
 * Parse a single line of markdown-ish text into React nodes.
 * Handles: [text](url), bare https:// URLs, **bold**, and ![alt](src) images.
 */
const MD_TOKEN =
  /(\!\[[^\]]*\]\(https?:\/\/[^)]+\)|\[[^\]]*\]\(https?:\/\/[^)]+\)|\*\*[^*]+\*\*|https?:\/\/[^\s)\]>"]+)/g;

function MarkdownLine({ text }: { text: string }): ReactNode {
  const parts = text.split(MD_TOKEN);
  return (
    <>
      {parts.map((part, i) => {
        if (!part) return null;
        // Markdown image: ![alt](src)
        const imgMatch = part.match(/^\!\[([^\]]*)\]\((https?:\/\/[^)]+)\)$/);
        if (imgMatch) {
          return (
            <img
              key={i}
              src={imgMatch[2]}
              alt={imgMatch[1]}
              style={{ maxWidth: "100%", marginTop: 8, marginBottom: 8, borderRadius: 4 }}
            />
          );
        }
        // Markdown link: [text](url)
        const linkMatch = part.match(/^\[([^\]]*)\]\((https?:\/\/[^)]+)\)$/);
        if (linkMatch) {
          return (
            <a key={i} href={linkMatch[2]} target="_blank" rel="noreferrer">
              {linkMatch[1]}
            </a>
          );
        }
        // Bold: **text**
        const boldMatch = part.match(/^\*\*(.+)\*\*$/);
        if (boldMatch) {
          return <strong key={i}>{boldMatch[1]}</strong>;
        }
        // Bare URL
        if (/^https?:\/\//.test(part)) {
          return (
            <a key={i} href={part} target="_blank" rel="noreferrer">
              {part}
            </a>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

/** Render multi-line markdown-ish text with clickable links, bold, and images. */
function RichText({ text }: { text: string }) {
  // Strip markdown heading prefixes (## etc.)
  const lines = text.split("\n").map((l) => l.replace(/^#{1,4}\s+/, ""));
  return (
    <div className="qs-analysis">
      {lines.map((line, i) =>
        line.trim() === "" ? null : (
          <p key={i}>
            <MarkdownLine text={line} />
          </p>
        )
      )}
    </div>
  );
}

export default function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const locState = location.state as {
    address?: string;
    quickscanSections?: QuickscanSection[];
  } | null;

  const [address, setAddress] = useState(locState?.address ?? "");

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

  // Download loading states
  const [zipLoading, setZipLoading] = useState(false);
  const [downloadingFiles, setDownloadingFiles] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!jobId) return;
    api
      .get(`/api/files/${jobId}`)
      .then((r) => setFiles(r.data.files))
      .catch(() => setFiles([]))
      .finally(() => setLoadingFiles(false));

    // Load job metadata (address etc.) if not in navigation state
    if (!address) {
      api
        .get(`/api/jobs/${jobId}`)
        .then((r) => {
          if (r.data.address) setAddress(r.data.address);
          else if (r.data.x && r.data.y)
            setAddress(`RD (${r.data.x.toFixed(0)}, ${r.data.y.toFixed(0)})`);
        })
        .catch(() => {});
    }

    // Load cached quickscan if not already in navigation state
    if (!qsSections) {
      api
        .get(`/api/quickscan/${jobId}/cached`)
        .then((r) => {
          if (r.data.sections) setQsSections(r.data.sections);
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  /** Get a short-lived download token for authenticated downloads */
  const getDownloadToken = async (): Promise<string> => {
    const res = await api.post("/api/auth/download-token");
    return res.data.token;
  };

  /**
   * Base URL that hits the backend directly (bypassing Vite proxy).
   * Native browser downloads need real Content-Disposition headers which
   * the Vite dev proxy can strip/buffer. In production the proxy (nginx)
   * works fine, so we keep the same origin.
   */
  const downloadBase = import.meta.env.DEV
    ? "http://localhost:8009"
    : (import.meta.env.VITE_API_URL ?? "");

  /** For inline image loading we still go through Vite proxy */
  const apiBase = import.meta.env.VITE_API_URL ?? "";

  const downloadFile = async (filename: string) => {
    setDownloadingFiles((prev) => new Set(prev).add(filename));
    try {
      const token = await getDownloadToken();
      window.location.href =
        `${downloadBase}/api/files/${jobId}/download/${filename}?token=${encodeURIComponent(token)}`;
    } finally {
      setTimeout(() => {
        setDownloadingFiles((prev) => {
          const next = new Set(prev);
          next.delete(filename);
          return next;
        });
      }, 1500);
    }
  };

  const downloadZip = async () => {
    setZipLoading(true);
    try {
      const token = await getDownloadToken();
      window.location.href =
        `${downloadBase}/api/files/${jobId}/zip?token=${encodeURIComponent(token)}`;
    } finally {
      setTimeout(() => setZipLoading(false), 2000);
    }
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
      const token = await getDownloadToken();
      window.location.href =
        `${downloadBase}/api/quickscan/${jobId}/export?token=${encodeURIComponent(token)}`;
    } catch {
      alert("PPTX export mislukt.");
    } finally {
      setTimeout(() => setPptxLoading(false), 2000);
    }
  };

  /** Build an authenticated image URL via object URL */
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});

  const loadImage = async (filename: string) => {
    if (imageUrls[filename]) return;
    try {
      const token = await getDownloadToken();
      const res = await fetch(
        `${apiBase}/api/files/${jobId}/download/${filename}?token=${encodeURIComponent(token)}`
      );
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
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
        <RichText text={s.analysis} />
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
          <RichText text={s.omgevingsvisie} />
        </div>
      )}

      {/* History: web search results */}
      {s.history_web && (
        <div className="qs-history-web">
          <h4>Historische informatie</h4>
          <RichText text={s.history_web} />
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
            {/* Primary download buttons */}
            <div className="primary-downloads">
              <button
                className="btn btn-primary"
                onClick={downloadZip}
                disabled={zipLoading}
              >
                {zipLoading ? (
                  <><span className="spinner spinner-inline" /> Downloaden…</>
                ) : (
                  "Download onderlegger (ZIP)"
                )}
              </button>
            </div>
            <p className="download-hint">
              Het ZIP-bestand bevat het DXF-bestand{files.find((f) => f.extension === ".dwg") ? ", DWG-bestand" : ""} en
              alle kaartafbeeldingen. Pak het uit in één map zodat de afbeeldingen
              automatisch worden geladen in AutoCAD.
            </p>

            <details className="file-details">
              <summary>Alle bestanden ({files.length})</summary>
              <ul className="file-list">
                {files.map((f) => (
                  <li key={f.filename}>
                    <span className="file-name">{f.filename}</span>
                    <span className="file-size">{fmtSize(f.size_bytes)}</span>
                    <button
                      className="btn btn-outline btn-sm"
                      onClick={() => downloadFile(f.filename)}
                      disabled={downloadingFiles.has(f.filename)}
                    >
                      {downloadingFiles.has(f.filename) ? (
                        <><span className="spinner spinner-inline" /> Bezig…</>
                      ) : (
                        "Download"
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </details>
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
            <button
              className="btn btn-secondary"
              onClick={exportPptx}
              disabled={pptxLoading}
              style={{ marginBottom: "var(--space-md)" }}
            >
              {pptxLoading ? (
                <><span className="spinner spinner-inline" /> Exporteren…</>
              ) : (
                "Exporteer als PowerPoint (.pptx)"
              )}
            </button>

            {qsSections.map((s, i) =>
              isLocationInfo(s) ? (
                <div key={i}>{renderLocationInfo(s)}</div>
              ) : (
                <div key={i}>{renderAnalysisSection(s)}</div>
              )
            )}
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
