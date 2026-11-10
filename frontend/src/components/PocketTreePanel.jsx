import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { useT } from "../lib/i18n.jsx";
import QrCode from "./QrCode.jsx";

/** Scan-to-open link for the standalone pocket-tree page (see pages/PhoneTree.jsx). The link is anonymous
 * (minted by /api/rooms/{code}/phone-tree for the signed-in caller); only generating it needs login. */
export default function PocketTreePanel({ code }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setUrl("");
    setFailed(false);
    api(`/api/rooms/${encodeURIComponent(code)}/phone-tree`)
      .then((d) => { if (alive) setUrl(`${window.location.origin}/tree/${d.token}`); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [code]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard unavailable: the link is on screen anyway */ }
  };

  if (failed) return null;
  return (
    <section className="panel invite-qr">
      <h3>{t("Pocket tree")}</h3>
      <p className="muted small">{t("Scan with your phone and keep that tab open while you study. Leave it and it withers — you'll be reminded here to get back to work.")}</p>
      {url && (
        <>
          <QrCode value={url} label={t("QR code to open your pocket tree")} />
          <p className="small join-url"><code>{url}</code></p>
          <button className="btn small" onClick={copy}>{copied ? t("Copied") : t("Copy link")}</button>
        </>
      )}
    </section>
  );
}
