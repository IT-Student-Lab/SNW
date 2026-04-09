import { Outlet, NavLink, Navigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import "./AppLayout.css";

export default function AppLayout() {
  const { token, username, logout } = useAuth();

  if (!token) return <Navigate to="/login" replace />;

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
            <NavLink to="/" end>
              Generator
            </NavLink>
          </nav>
          <div className="topbar-user">
            <span>{username ?? "…"}</span>
            <button className="btn btn-outline btn-sm" onClick={logout}>
              Uitloggen
            </button>
          </div>
        </div>
      </header>
      <main className="main-content container">
        <Outlet />
      </main>
    </div>
  );
}
