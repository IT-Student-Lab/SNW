import { useState, useEffect, useRef } from "react";
import api from "../api";
import "./TemplatePage.css";

interface TemplateInfo {
  type: "default" | "custom";
  filename: string | null;
  size: number | null;
  uploaded_at: string | null;
}

export default function TemplatePage() {
  const [info, setInfo] = useState<TemplateInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchInfo = () => {
    setLoading(true);
    api
      .get("/api/template")
      .then((r) => setInfo(r.data))
      .catch(() => setError("Kon sjabloon-info niet ophalen"))
      .finally(() => setLoading(false));
  };

  useEffect(fetchInfo, []);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;

    const ext = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();
    if (ext !== ".dwt" && ext !== ".dwg") {
      setError("Alleen .dwt en .dwg bestanden zijn toegestaan");
      return;
    }

    setUploading(true);
    setError("");
    setSuccess("");

    const formData = new FormData();
    formData.append("file", file);

    try {
      await api.post("/api/template", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setSuccess("Sjabloon geüpload");
      if (fileRef.current) fileRef.current.value = "";
      fetchInfo();
    } catch (e: unknown) {
      const msg = e instanceof Object && "response" in e
        ? (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setError(msg || "Upload mislukt");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!confirm("Weet je zeker dat je het custom sjabloon wilt verwijderen?")) return;
    setError("");
    setSuccess("");
    try {
      await api.delete("/api/template");
      setSuccess("Custom sjabloon verwijderd — standaard wordt weer gebruikt");
      fetchInfo();
    } catch (e: unknown) {
      const msg = e instanceof Object && "response" in e
        ? (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
        : undefined;
      setError(msg || "Verwijderen mislukt");
    }
  };

  const fmtSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const fmtDate = (iso: string) =>
    new Date(iso).toLocaleString("nl-NL", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });

  return (
    <div className="container template-page">
      <h1>DXF Sjabloon</h1>
      <p className="template-subtitle">
        Upload een AutoCAD sjabloon (.dwt/.dwg) om de lagenstructuur van je
        organisatie te gebruiken als basis voor gegenereerde DXF-bestanden.
      </p>

      {error && <div className="template-alert template-alert-error">{error}</div>}
      {success && <div className="template-alert template-alert-success">{success}</div>}

      {loading ? (
        <p>Laden…</p>
      ) : info ? (
        <div className="card template-card">
          <h3>Huidig sjabloon</h3>
          {info.type === "custom" ? (
            <div className="template-info">
              <div className="template-info-row">
                <span className="template-label">Bestand:</span>
                <span>{info.filename}</span>
              </div>
              <div className="template-info-row">
                <span className="template-label">Grootte:</span>
                <span>{info.size ? fmtSize(info.size) : "—"}</span>
              </div>
              <div className="template-info-row">
                <span className="template-label">Geüpload:</span>
                <span>{info.uploaded_at ? fmtDate(info.uploaded_at) : "—"}</span>
              </div>
              <button className="btn btn-danger btn-sm" onClick={handleDelete}>
                Verwijderen
              </button>
            </div>
          ) : (
            <p className="template-default-msg">
              Standaard sjabloon actief — de 14 SNW-lagen worden automatisch
              aangemaakt bij elke generatie.
            </p>
          )}
        </div>
      ) : null}

      <div className="card template-card">
        <h3>Nieuw sjabloon uploaden</h3>
        <p className="template-hint">
          Dit vervangt het huidige sjabloon. Accepteert .dwt en .dwg bestanden (max 50 MB).
        </p>
        <div className="template-upload-row">
          <input
            ref={fileRef}
            type="file"
            accept=".dwt,.dwg"
            className="template-file-input"
          />
          <button
            className="btn btn-primary"
            onClick={handleUpload}
            disabled={uploading}
          >
            {uploading ? "Uploaden…" : "Uploaden"}
          </button>
        </div>
      </div>
    </div>
  );
}
