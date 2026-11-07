import Thales from "./Thales.jsx";
import FunctionPlot from "./FunctionPlot.jsx";
import ProcessFlow from "./ProcessFlow.jsx";
import ConceptMap from "./ConceptMap.jsx";
import { tx, useT } from "../lib/i18n.jsx";

const RENDERERS = { thales: Thales, function_plot: FunctionPlot, process_flow: ProcessFlow, concept_map: ConceptMap };
const NAMES = {
  thales: tx("Interactive figure"),
  function_plot: tx("Interactive graph"),
  process_flow: tx("Step-through diagram"),
  concept_map: tx("Concept map"),
};

/** The drawing board that sits beside every explanation. `focus` comes from the step being read. */
export default function VisualSheet({ visual, fallback, focus }) {
  const t = useT();
  const Renderer = RENDERERS[visual.template] || ConceptMap;
  return (
    <figure className="sheet">
      <figcaption className="sheet-cap">
        <span>{t(NAMES[visual.template] || tx("Illustration"))}</span>
        {fallback && <span className="muted small">{t("Simplified map built from the key points")}</span>}
      </figcaption>
      <Renderer key={JSON.stringify(visual)} params={visual.params} focus={focus} />
    </figure>
  );
}
