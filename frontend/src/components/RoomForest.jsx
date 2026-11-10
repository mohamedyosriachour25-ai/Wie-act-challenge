import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useT } from "../lib/i18n.jsx";
import QrCode from "./QrCode.jsx";
import Tree from "./Tree.jsx";

/** The live room's own forest. Each member can scan the one QR code here to grow a pocket tree on their
 * phone (pages/PhoneTree.jsx); the room shows everyone's tree live (`phone_trees` messages from
 * phonetree.py) plus how many trees grew or withered here. The only button copies the room's invite link. */
export default function RoomForest({ code, trees, log, me }) {
  const t = useT();
  const [treeUrl, setTreeUrl] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    setTreeUrl("");
    api(`/api/rooms/${encodeURIComponent(code)}/phone-tree`)
      .then((d) => { if (alive) setTreeUrl(`${window.location.origin}/tree/${d.token}`); })
      .catch(() => {});
    return () => { alive = false; };
  }, [code]);

  const invite = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/live/${encodeURIComponent(code)}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable (http on another device): nothing else to do */ }
  };

  const grown = log.filter((e) => e.state === "grown").length;
  const wilted = log.filter((e) => e.state === "wilted").length;
  const mine = trees.find((x) => x.pseudo === me);
  const qr = (
    <div className="forest-qr">
      <QrCode value={treeUrl} label={t("QR code to open your pocket tree")} />
      <a className="small" href={treeUrl} target="_blank" rel="noreferrer">{t("Open my tree on this device")}</a>
    </div>
  );
  const statusOf = (x) => (x.state === "wilted" ? t("Withered")
    : !x.visible ? t("Phone away")
    : x.state === "grown" ? t("Fully grown")
    : t("Growing {n}%", { n: Math.round(x.p * 100) }));

  return (
    <section className="panel room-forest" aria-labelledby="forest-title">
      <h3 id="forest-title">{t("Room forest")}</h3>
      <p className="muted small">{t("Scan the code with your phone and keep that page open while you study. During focus, leaving it withers your tree; breaks are free.")}</p>

      {trees.length > 0 ? (
        <ul className="pocket-grid">
          {trees.map((x) => (
            <li key={x.pseudo} className={`${x.pseudo === me ? "me" : ""} ${!x.visible && x.state !== "wilted" ? "away" : ""}`}>
              <Tree p={x.p} state={x.state} label={t("{pseudo}'s tree: {status}", { pseudo: x.pseudo, status: statusOf(x) })} />
              <span className="who">{x.pseudo === me ? t("You") : x.pseudo}</span>
              <span className="tree-state small">{statusOf(x)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="small forest-empty">{t("No trees yet. Be the first to plant one.")}</p>
      )}
      {(grown > 0 || wilted > 0) && (
        <p className="small muted forest-counts">{t("Grown here: {g} · Withered: {w}", { g: grown, w: wilted })}</p>
      )}

      {treeUrl && (!mine || mine.state === "wilted" ? qr : (
        <>
          <p className="small tree-status">{t("Your tree is planted. Keep the page open on your phone.")}</p>
          <details className="forest-again"><summary className="small">{t("Show my code again")}</summary>{qr}</details>
        </>
      ))}

      <button className="btn small invite-btn" onClick={invite}>{copied ? t("Invite link copied") : t("Copy invite link")}</button>
    </section>
  );
}
