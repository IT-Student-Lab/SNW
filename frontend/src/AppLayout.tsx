import { useState, type FormEvent } from "react";
import { Outlet, Navigate, NavLink } from "react-router-dom";
import { useAuth } from "./AuthContext";
import api from "./api";
import "./AppLayout.css";

export default function AppLayout() {
  const { token, username, logout } = useAuth();
  const [showPwModal, setShowPwModal] = useState(false);
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [pwError, setPwError] = useState("");
  const [pwSuccess, setPwSuccess] = useState("");
  const [pwLoading, setPwLoading] = useState(false);

  if (!token) return <Navigate to="/login" replace />;

  const openModal = () => {
    setCurrentPw("");
    setNewPw("");
    setConfirmPw("");
    setPwError("");
    setPwSuccess("");
    setShowPwModal(true);
  };

  const handleChangePw = async (e: FormEvent) => {
    e.preventDefault();
    setPwError("");
    setPwSuccess("");

    if (newPw.length < 6) {
      setPwError("Nieuw wachtwoord moet minimaal 6 tekens zijn");
      return;
    }
    if (newPw !== confirmPw) {
      setPwError("Wachtwoorden komen niet overeen");
      return;
    }

    setPwLoading(true);
    try {
      await api.post("/api/auth/change-password", {
        current_password: currentPw,
        new_password: newPw,
      });
      setPwSuccess("Wachtwoord succesvol gewijzigd!");
      setTimeout(() => setShowPwModal(false), 1500);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      const detail = axiosErr?.response?.data?.detail;
      setPwError(detail ?? "Er ging iets mis");
    } finally {
      setPwLoading(false);
    }
  };

  return (
    <div className="app-layout">
      <header className="topbar">
        <div className="topbar-inner">
          <img
            src="/logo.png"
            alt="Studio Nico Wissing"
            className="topbar-logo"
          />
          <nav className="topbar-nav">
            <NavLink to="/" end>Generator</NavLink>
            <NavLink to="/history">Historie</NavLink>
            <NavLink to="/sjabloon">Sjabloon</NavLink>
          </nav>
          <div className="topbar-spacer" />
          <div className="topbar-user">
            <span>{username ?? "…"}</span>
            <button
              className="btn btn-outline btn-sm"
              onClick={openModal}
            >
              Wachtwoord wijzigen
            </button>
            <button className="btn btn-outline btn-sm" onClick={logout}>
              Uitloggen
            </button>
          </div>
        </div>
      </header>

      {showPwModal && (
        <div className="modal-overlay" onClick={() => setShowPwModal(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <h2>Wachtwoord wijzigen</h2>
            <form onSubmit={handleChangePw}>
              <div className="form-group">
                <label className="form-label">Huidig wachtwoord</label>
                <input
                  className="form-input"
                  type="password"
                  value={currentPw}
                  onChange={(e) => setCurrentPw(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label className="form-label">Nieuw wachtwoord</label>
                <input
                  className="form-input"
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  required
                  minLength={6}
                />
              </div>
              <div className="form-group">
                <label className="form-label">Bevestig nieuw wachtwoord</label>
                <input
                  className="form-input"
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  required
                />
              </div>
              {pwError && <div className="error-msg">{pwError}</div>}
              {pwSuccess && <div className="success-msg">{pwSuccess}</div>}
              <div className="modal-actions">
                <button
                  type="button"
                  className="btn btn-outline"
                  onClick={() => setShowPwModal(false)}
                >
                  Annuleren
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={pwLoading}
                >
                  {pwLoading ? "Bezig…" : "Wijzigen"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <main className="main-content container">
        <Outlet />
      </main>
    </div>
  );
}
