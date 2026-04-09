import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import api from "./api";

interface AuthCtx {
  token: string | null;
  username: string | null;
  login: (user: string, pass: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem("snw_token")
  );
  const [username, setUsername] = useState<string | null>(null);

  // Fetch user info when token present
  useEffect(() => {
    if (!token) {
      setUsername(null);
      return;
    }
    api
      .get("/api/auth/me")
      .then((r) => setUsername(r.data.username))
      .catch(() => {
        localStorage.removeItem("snw_token");
        setToken(null);
      });
  }, [token]);

  const login = async (user: string, pass: string) => {
    const res = await api.post("/api/auth/login", {
      username: user,
      password: pass,
    });
    const t = res.data.access_token;
    localStorage.setItem("snw_token", t);
    setToken(t);
  };

  const logout = () => {
    localStorage.removeItem("snw_token");
    setToken(null);
    setUsername(null);
  };

  return (
    <AuthContext.Provider value={{ token, username, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
