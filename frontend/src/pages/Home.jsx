import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth.jsx";
import { useT } from "../lib/i18n.jsx";
import { AuthCard, Dots } from "../components/Common.jsx";
import { ProgressPanel, ScorePanel } from "../components/Study.jsx";
import VisualSheet from "../visuals/VisualSheet.jsx";

const HERO = { template: "thales", params: { ratio: 0.5, labels: ["A", "B", "C"], right_angle: false } };

export default function Home() {
  const { user, ready } = useAuth();
  const t = useT();
  return (
    <>
      <section className="hero">
        <div className="hero-text">
          <h1>{t("Every explanation comes with a picture you can move.")}</h1>
          <p className="lead">
            {t("Ask about a topic. HerwieGO explains it in short steps and draws it beside the text, so you can drag it, test it and see why it works. Study alone, with friends, or in a quiet room full of people doing the same.")}
          </p>
          <div className="row wrap">
            <Link className="btn primary big" to="/study">{t("Start studying")}</Link>
            <Link className="btn big" to="/live">{t("Join a live room")}</Link>
          </div>
          <p className="muted small">{t("Try the figure: drag the dot on the left side of the triangle.")}</p>
        </div>
        <VisualSheet visual={HERO} />
      </section>

      <section className="home-grid">
        <div>
          <h2 className="section-title">{t("Three ways to study")}</h2>
          <ul className="spaces">
            <li>
              <div>
                <h3><Link to="/study">{t("Solo study")}</Link></h3>
                <p>{t("Ask about any topic and get steps, a figure, hints when you are stuck, and a quiz to check yourself. It adapts to how you learn and keeps your progress.")}</p>
              </div>
              <Dots n={1} label={t("One person")} />
            </li>
            <li>
              <div>
                <h3><Link to="/rooms">{t("Study rooms")}</Link></h3>
                <p>{t("Two to ten people on the same topic. One shared explanation, a shared quiz, a chat with an anonymous option, and a timer everyone sees.")}</p>
              </div>
              <Dots n={10} filled={4} label={t("Two to ten people")} />
            </li>
            <li>
              <div>
                <h3><Link to="/live">{t("Live focus rooms")}</Link></h3>
                <p>{t("Public rooms on a shared timer with short breaks and a small game. Everyone works on their own thing. Your focus signal is computed on your device and never leaves it.")}</p>
              </div>
              <Dots n={40} filled={40} max={14} label={t("Many people")} />
            </li>
          </ul>
        </div>
        <aside>
          {ready && (user ? <><ProgressPanel /><ScorePanel /></> : <AuthCard note={t("A pseudo and a password. No e-mail, no real name.")} />)}
        </aside>
      </section>
    </>
  );
}
