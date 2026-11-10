import { NavLink, Navigate, Route, Routes, Link, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth.jsx";
import { LangSwitch, useT } from "./lib/i18n.jsx";
import AIStatus from "./components/AIStatus.jsx";
import Logo from "./components/Logo.jsx";
import Backdrop from "./components/Backdrop.jsx";
import Home from "./pages/Home.jsx";
import Study from "./pages/Study.jsx";
import Rooms from "./pages/Rooms.jsx";
import Room from "./pages/Room.jsx";
import { Live, LiveRoom } from "./pages/Live.jsx";
import Review from "./pages/Review.jsx";
import { PhoneTreePage } from "./pages/PhoneTree.jsx";

function Header() {
  const { user, logout } = useAuth();
  const t = useT();
  return (
    <header className="top">
      <Link to="/" className="brand" aria-label={t("HerwieGO home")}>
        <Logo size={34} />
      </Link>
      <nav aria-label={t("Main")}>
        <NavLink to="/" end>{t("Home")}</NavLink>
        <NavLink to="/study">{t("Solo study")}</NavLink>
        <NavLink to="/rooms">{t("Study rooms")}</NavLink>
        <NavLink to="/live">{t("Live")}</NavLink>
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
  const location = useLocation();
  // The pocket-tree space is reached only by its own QR code or link (never the main nav), is meant to
  // be opened on a phone kept open during study, and needs no login — so it gets no app header/footer.
  if (location.pathname.startsWith("/tree/")) {
    return (
      <main id="main">
        <Routes>
          <Route path="/tree/:token" element={<PhoneTreePage />} />
        </Routes>
      </main>
    );
  }
  return (
    <>
      <Backdrop />
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
          {/* every live room has its own forest now; old forest links land on the live rooms */}
          <Route path="/forest/*" element={<Navigate to="/live" replace />} />
          <Route path="/review" element={<Review />} />
          <Route path="*" element={<div className="narrow"><h1>{t("Page not found")}</h1><Link className="btn" to="/">{t("Go home")}</Link></div>} />
        </Routes>
      </main>
      <footer className="foot muted small">
        {t("HerwieGO is a study companion. It supports learning and does not replace teachers. Only pseudonyms are stored.")}
      </footer>
    </>
  );
}
