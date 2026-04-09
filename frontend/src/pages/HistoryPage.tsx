import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../api";
import "./HistoryPage.css";

interface JobMeta {
  job_id: string;
  address: string;
  x: number;
  y: number;
  radius: number;
  user: string;
  created_at: string;
  has_quickscan: boolean;
}

export default function HistoryPage() {
  const navigate = useNavigate();
  const [jobs, setJobs] = useState<JobMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    api
      .get("/api/jobs")
      .then((r) => setJobs(r.data.jobs))
      .catch(() => setJobs([]))
      .finally(() => setLoading(false));
  }, []);

  const confirmDelete = async (jobId: string) => {
    if (!confirm("Weet je zeker dat je deze job wilt verwijderen?")) return;
    setDeleting(jobId);
    try {
      await api.delete(`/api/jobs/${jobId}`);
      setJobs((prev) => prev.filter((j) => j.job_id !== jobId));
    } catch {
      alert("Verwijderen mislukt.");
    } finally {
      setDeleting(null);
    }
  };

  const fmtDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString("nl-NL", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  const filtered = jobs.filter(
    (j) =>
      !search ||
      j.address.toLowerCase().includes(search.toLowerCase()) ||
      j.job_id.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="history-page">
      <div className="history-header">
        <h1>Historie</h1>
        <input
          className="form-input history-search"
          type="text"
          placeholder="Zoeken op adres of job ID…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {loading ? (
        <div className="spinner" />
      ) : filtered.length === 0 ? (
        <p className="history-empty">
          {jobs.length === 0
            ? "Nog geen generaties uitgevoerd."
            : "Geen resultaten gevonden."}
        </p>
      ) : (
        <div className="history-table-wrap card">
          <table className="history-table">
            <thead>
              <tr>
                <th>Datum</th>
                <th>Adres / Locatie</th>
                <th>Straal</th>
                <th>AI Scan</th>
                <th>Acties</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((j) => (
                <tr key={j.job_id}>
                  <td className="col-date">{fmtDate(j.created_at)}</td>
                  <td className="col-address">
                    {j.address || `RD (${j.x.toFixed(0)}, ${j.y.toFixed(0)})`}
                  </td>
                  <td className="col-radius">{j.radius} m</td>
                  <td className="col-qs">
                    {j.has_quickscan ? (
                      <span className="badge badge-green">Ja</span>
                    ) : (
                      <span className="badge badge-gray">Nee</span>
                    )}
                  </td>
                  <td className="col-actions">
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() =>
                        navigate(`/results/${j.job_id}`, {
                          state: { address: j.address },
                        })
                      }
                    >
                      Bekijken
                    </button>
                    <button
                      className="btn btn-danger btn-sm"
                      onClick={() => confirmDelete(j.job_id)}
                      disabled={deleting === j.job_id}
                    >
                      {deleting === j.job_id ? "…" : "Verwijderen"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
