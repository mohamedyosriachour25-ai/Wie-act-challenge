import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(!getToken());

  useEffect(() => {
    if (!getToken()) return;
    api("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(""))
      .finally(() => setReady(true));
  }, []);

  const start = useCallback(async (mode, pseudo, password) => {
    const session = await api(`/api/auth/${mode}`, { method: "POST", body: { pseudo, password } });
    setToken(session.token);
    setUser(session.user);
  }, []);

  const logout = useCallback(() => {
    setToken("");
    setUser(null);
  }, []);

  const setStyle = useCallback((style) => setUser((u) => (u ? { ...u, learning_style: style } : u)), []);

  return <AuthContext.Provider value={{ user, ready, start, logout, setStyle }}>{children}</AuthContext.Provider>;
}
