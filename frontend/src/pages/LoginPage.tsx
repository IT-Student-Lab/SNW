import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import "./LoginPage.css";

export default function LoginPage() {
  const { token, login } = useAuth();
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  if (token) return <Navigate to="/" replace />;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(user, pass);
    } catch {
      setError("Ongeldige inloggegevens");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card card" onSubmit={handleSubmit}>
        <img
          src="/logo.png"
          alt="Studio Nico Wissing"
          className="login-logo"
        />
        <h2>CAD Onderlegger</h2>

        {error && <div className="error-msg">{error}</div>}

        <div className="form-group">
          <label className="form-label" htmlFor="username">
            Gebruikersnaam
          </label>
          <input
            id="username"
            className="form-input"
            type="text"
            autoComplete="username"
            value={user}
            onChange={(e) => setUser(e.target.value)}
            required
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="password">
            Wachtwoord
          </label>
          <input
            id="password"
            className="form-input"
            type="password"
            autoComplete="current-password"
            value={pass}
            onChange={(e) => setPass(e.target.value)}
            required
          />
        </div>

        <button className="btn btn-primary" disabled={loading} type="submit">
          {loading ? "Bezig…" : "Inloggen"}
        </button>
      </form>
    </div>
  );
}
