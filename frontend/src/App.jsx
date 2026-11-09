import { NavLink, Navigate, Route, Routes, Link } from "react-router-dom";
import { useAuth } from "./lib/auth.jsx";
import { LangSwitch, useT } from "./lib/i18n.jsx";
import AIStatus from "./components/AIStatus.jsx";
import Home from "./pages/Home.jsx";
import Study from "./pages/Study.jsx";
import Rooms from "./pages/Rooms.jsx";
import Room from "./pages/Room.jsx";
import { Live, LiveRoom } from "./pages/Live.jsx";
import { ForestRoom } from "./pages/Forest.jsx";
import Review from "./pages/Review.jsx";

function Header() {
  const { user, logout } = useAuth();
  const t = useT();
  return (
    <header className="top">
      <Link to="/" className="brand" aria-label={t("EdNova home")}>
        <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
          <rect width="32" height="32" rx="7" fill="var(--ultra)" />
          <path d="M16 6 6 24h20z" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinejoin="round" />
          <path d="M11 15h10" stroke="var(--marker)" strokeWidth="2.4" />
        </svg>
        EdNova
      </Link>
      <nav aria-label={t("Main")}>
        <NavLink to="/" end>{t("Home")}</NavLink>
        <NavLink to="/study">{t("Solo study")}</NavLink>
        <NavLink to="/rooms">{t("Study rooms")}</NavLink>
        <NavLink to="/live">{t("Live")}</NavLink>
        <NavLink to="/forest">{t("Forest")}</NavLink>
        <NavLink to="/review">{t("Revision")}</NavLink>
      </nav>
      <div className="top-right">
        <LangSwitch />
        <AIStatus />
        {user && (
          <span className="me">
            <span className="avatar" aria-hidden="true">{user.pseudo[0].toUpperCase()}</span>
            {user.pseudo}
            <button className="link" onClick={logout}>{t("Sign out")}</button>
          </span>
        )}
      </div>
    </header>
  );
}

export default function App() {
  const t = useT();
  return (
    <>
      <a className="skip" href="#main">{t("Skip to content")}</a>
      <Header />
      <main id="main">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/study" element={<Study />} />
          <Route path="/rooms" element={<Rooms />} />
          <Route path="/rooms/:code" element={<Room />} />
          <Route path="/live" element={<Live />} />
          <Route path="/live/:code" element={<LiveRoom />} />
          <Route path="/forest" element={<Navigate to="/forest/LIVE-FOREST" replace />} />
          <Route path="/forest/:code" element={<ForestRoom />} />
          <Route path="/review" element={<Review />} />
          <Route path="*" element={<div className="narrow"><h1>{t("Page not found")}</h1><Link className="btn" to="/">{t("Go home")}</Link></div>} />
        </Routes>
      </main>
      <footer className="foot muted small">
        {t("EdNova is a study companion. It supports learning and does not replace teachers. Only pseudonyms are stored.")}
      </footer>
    </>
  );
}
