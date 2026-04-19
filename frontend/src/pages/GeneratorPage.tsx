import { useState, useEffect, useRef, useCallback, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import "./GeneratorPage.css";

interface Suggestion {
  id: string;
  display_name: string;
  type: string;
}

interface ProgressStep {
  message: string;
  done: boolean;
}

export default function GeneratorPage() {
  const navigate = useNavigate();

  // Mode
  const [mode, setMode] = useState<"address" | "coords">("address");

  // Address autocomplete
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const debounce = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Whether a valid address was picked from suggestions
  const [addressPicked, setAddressPicked] = useState(false);

  // Coords
  const [rdX, setRdX] = useState("");
  const [rdY, setRdY] = useState("");

  // Shared
  const [radius, setRadius] = useState("250");
  const [dxfName, setDxfName] = useState("onderlegger.dxf");

  // State
  const [generating, setGenerating] = useState(false);
  const [steps, setSteps] = useState<ProgressStep[]>([]);
  const [error, setError] = useState("");

  // Preview
  const [previewTopoUrl, setPreviewTopoUrl] = useState<string | null>(null);
  const [previewLuchtUrl, setPreviewLuchtUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewAbort = useRef<AbortController | null>(null);

  // PDOK suggest
  useEffect(() => {
    if (mode !== "address" || query.length < 3) {
      setSuggestions([]);
      return;
    }
    clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      try {
        const res = await api.get("/api/geocode/suggest", {
          params: { q: query },
        });
        setSuggestions(res.data.suggestions);
      } catch {
        setSuggestions([]);
      }
    }, 300);
    return () => clearTimeout(debounce.current);
  }, [query, mode]);

  const pickSuggestion = (s: Suggestion) => {
    setQuery(s.display_name);
    setSuggestions([]);
    setAddressPicked(true);
  };

  // Fetch preview map when location + radius are known
  const fetchPreview = useCallback(async () => {
    // Determine if we have enough info
    const hasLocation =
      (mode === "address" && addressPicked && query.length >= 3) ||
      (mode === "coords" && rdX !== "" && rdY !== "");
    if (!hasLocation) return;

    previewAbort.current?.abort();
    const ctrl = new AbortController();
    previewAbort.current = ctrl;
    setPreviewLoading(true);

    try {
      const body: Record<string, unknown> = {
        mode,
        radius: Number(radius),
      };
      if (mode === "address") {
        body.address = query;
      } else {
        body.x = Number(rdX);
        body.y = Number(rdY);
      }

      const token = localStorage.getItem("snw_token");
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      };

      const [topoResp, luchtResp] = await Promise.all([
        fetch("/api/preview?layer=topo", {
          method: "POST",
          headers,
          body: JSON.stringify(body),
          signal: ctrl.signal,
        }),
        fetch("/api/preview?layer=luchtfoto", {
          method: "POST",
          headers,
          body: JSON.stringify(body),
          signal: ctrl.signal,
        }),
      ]);

      if (topoResp.ok) {
        const blob = await topoResp.blob();
        const url = URL.createObjectURL(blob);
        setPreviewTopoUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      }
      if (luchtResp.ok) {
        const blob = await luchtResp.blob();
        const url = URL.createObjectURL(blob);
        setPreviewLuchtUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return url;
        });
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      setPreviewTopoUrl(null);
      setPreviewLuchtUrl(null);
    } finally {
      setPreviewLoading(false);
    }
  }, [mode, query, addressPicked, rdX, rdY, radius]);

  // Debounced preview trigger
  const previewTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(fetchPreview, 500);
    return () => clearTimeout(previewTimer.current);
  }, [fetchPreview]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSteps([]);
    setGenerating(true);

    const body: Record<string, unknown> = {
      mode,
      radius: Number(radius),
      dxf_name: dxfName,
    };

    if (mode === "address") {
      body.address = query;
    } else {
      body.x = Number(rdX);
      body.y = Number(rdY);
    }

    const token = localStorage.getItem("snw_token");

    try {
      // Use fetch for SSE (EventSource doesn't support POST)
      const resp = await fetch("/api/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => null);
        throw new Error(errData?.detail ?? `HTTP ${resp.status}`);
      }

      const reader = resp.body?.getReader();
      if (!reader) throw new Error("Geen stream beschikbaar");

      const decoder = new TextDecoder();
      let buffer = "";
      let quickscanSections: unknown[] | null = null;
      let jobId = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";

        let currentEvent = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const raw = line.slice(6);
            try {
              const data = JSON.parse(raw);

              if (currentEvent === "progress") {
                setSteps((prev) => {
                  // Mark previous steps as done, add new one
                  const updated = prev.map((s) => ({ ...s, done: true }));
                  updated.push({ message: data.message, done: false });
                  return updated;
                });
              } else if (currentEvent === "quickscan") {
                quickscanSections = data.sections;
                setSteps((prev) => {
                  const updated = prev.map((s) => ({ ...s, done: true }));
                  updated.push({
                    message: "AI-analyse afgerond",
                    done: false,
                  });
                  return updated;
                });
              } else if (currentEvent === "quickscan_error") {
                // Non-fatal, ignore in progress
              } else if (currentEvent === "complete") {
                jobId = data.job_id;
                setSteps((prev) =>
                  prev.map((s) => ({ ...s, done: true }))
                );
              } else if (currentEvent === "error") {
                throw new Error(data.message);
              }
            } catch (parseErr) {
              if (
                parseErr instanceof Error &&
                parseErr.message.startsWith("Generatie")
              ) {
                throw parseErr;
              }
            }
            currentEvent = "";
          }
        }
      }

      if (jobId) {
        navigate(`/results/${jobId}`, {
          state: {
            address: mode === "address" ? query : `RD (${rdX}, ${rdY})`,
            quickscanSections,
          },
        });
      }
    } catch (err: unknown) {
      const detail =
        err instanceof Error
          ? err.message
          : "Er ging iets mis bij het genereren.";
      setError(detail);
      setGenerating(false);
    }
  };

  return (
    <>
      {generating && (
        <div className="loading-overlay">
          <div className="progress-panel">
            <div className="spinner" />
            <h2>Onderlegger wordt gegenereerd</h2>
            <ul className="progress-steps">
              {steps.map((s, i) => (
                <li key={i} className={s.done ? "step-done" : "step-active"}>
                  <span className="step-icon">
                    {s.done ? "✓" : "⟳"}
                  </span>
                  {s.message}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="generator-page">
        <h1>CAD Onderlegger Genereren</h1>

        <form className="card generator-form" onSubmit={handleSubmit}>
          {/* Mode toggle */}
          <div className="mode-toggle">
            <button
              type="button"
              className={`btn ${mode === "address" ? "btn-primary" : "btn-outline"}`}
              onClick={() => setMode("address")}
            >
              Adres
            </button>
            <button
              type="button"
              className={`btn ${mode === "coords" ? "btn-primary" : "btn-outline"}`}
              onClick={() => setMode("coords")}
            >
              RD-coördinaten
            </button>
          </div>

          {mode === "address" ? (
            <div className="form-group autocomplete-wrapper">
              <label className="form-label">Adres</label>
              <input
                className="form-input"
                type="text"
                placeholder="Zoek een adres…"
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setAddressPicked(false);
                }}
              />
              {suggestions.length > 0 && (
                <ul className="autocomplete-list">
                  {suggestions.map((s) => (
                    <li key={s.id} onClick={() => pickSuggestion(s)}>
                      <strong>{s.type}</strong> — {s.display_name}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : (
            <div className="coord-row">
              <div className="form-group">
                <label className="form-label">RD X</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder="155000"
                  value={rdX}
                  onChange={(e) => setRdX(e.target.value)}
                />
              </div>
              <div className="form-group">
                <label className="form-label">RD Y</label>
                <input
                  className="form-input"
                  type="number"
                  placeholder="463000"
                  value={rdY}
                  onChange={(e) => setRdY(e.target.value)}
                />
              </div>
            </div>
          )}

          {/* Radius */}
          <div className="form-group">
            <label className="form-label">
              Straal (m): {radius}
            </label>
            <input
              type="range"
              min="100"
              max="1000"
              step="50"
              value={radius}
              onChange={(e) => setRadius(e.target.value)}
            />
          </div>

          {/* Preview maps */}
          {(previewTopoUrl || previewLuchtUrl || previewLoading) && (
            <div className="preview-panel">
              <label className="form-label">Preview geselecteerd gebied ({radius}m straal)</label>
              <div className="preview-grid">
                <div className="preview-item">
                  <span className="preview-label">Topokaart</span>
                  <div className="preview-img-wrap">
                    {previewLoading && !previewTopoUrl && (
                      <div className="preview-spinner"><div className="spinner" /></div>
                    )}
                    {previewTopoUrl && (
                      <img src={previewTopoUrl} alt="Topokaart preview" className="preview-img" />
                    )}
                  </div>
                </div>
                <div className="preview-item">
                  <span className="preview-label">Luchtfoto</span>
                  <div className="preview-img-wrap">
                    {previewLoading && !previewLuchtUrl && (
                      <div className="preview-spinner"><div className="spinner" /></div>
                    )}
                    {previewLuchtUrl && (
                      <img src={previewLuchtUrl} alt="Luchtfoto preview" className="preview-img" />
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* DXF name */}
          <div className="form-group">
            <label className="form-label">DXF bestandsnaam</label>
            <input
              className="form-input"
              type="text"
              value={dxfName}
              onChange={(e) => setDxfName(e.target.value)}
            />
          </div>

          {error && <div className="error-msg">{error}</div>}

          <button className="btn btn-primary" type="submit" disabled={generating}>
            Genereer Onderlegger
          </button>
        </form>
      </div>
    </>
  );
}
