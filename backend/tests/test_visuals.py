"""The two freer-form illustration templates added alongside the real-AI-only gateway: timeline
(chronological events, e.g. for history topics) and comparison (side-by-side aspects)."""
from app.ai.visuals import describe_figure, sanitize_visual


def test_timeline_is_accepted_and_clamped():
    spec = sanitize_visual({
        "template": "timeline",
        "params": {"events": [{"date": f"Year {i}", "label": f"Event {i}", "detail": "x"} for i in range(10)]},
    })
    assert spec["template"] == "timeline" and len(spec["params"]["events"]) == 8  # clamped to the 8-event ceiling


def test_timeline_needs_at_least_three_events():
    assert sanitize_visual({"template": "timeline", "params": {"events": [{"date": "1", "label": "a"}]}}) is None
    assert sanitize_visual({"template": "timeline", "params": {}}) is None
    assert sanitize_visual({"template": "timeline", "params": {"events": "not-a-list"}}) is None


def test_timeline_events_without_a_label_are_dropped():
    spec = sanitize_visual({
        "template": "timeline",
        "params": {"events": [{"date": "1", "label": "a"}, {"date": "2"}, {"date": "3", "label": "c"}, {"date": "4", "label": "d"}]},
    })
    assert spec is not None and len(spec["params"]["events"]) == 3


def test_comparison_is_accepted_and_cells_are_padded_to_match_columns():
    spec = sanitize_visual({
        "template": "comparison",
        "params": {
            "columns": ["Before", "After"],
            "rows": [{"label": "Speed", "cells": ["slow"]}, {"label": "Cost", "cells": ["high", "low", "ignored"]}],
        },
    })
    assert spec["params"]["columns"] == ["Before", "After"]
    assert spec["params"]["rows"][0]["cells"] == ["slow", ""]        # short row padded
    assert spec["params"]["rows"][1]["cells"] == ["high", "low"]    # long row truncated to the column count


def test_comparison_needs_at_least_two_columns_and_two_rows():
    assert sanitize_visual({"template": "comparison", "params": {"columns": ["Only one"], "rows": [{"label": "a", "cells": ["x"]}]}}) is None
    assert sanitize_visual({"template": "comparison", "params": {"columns": ["A", "B"], "rows": [{"label": "a", "cells": ["x", "y"]}]}}) is None
    assert sanitize_visual({"template": "comparison", "params": {"columns": ["A", "B", "C", "D"], "rows": []}}) is None


def test_comparison_caps_at_three_columns_and_seven_rows():
    spec = sanitize_visual({
        "template": "comparison",
        "params": {
            "columns": ["A", "B", "C", "D"],
            "rows": [{"label": f"row{i}", "cells": ["x", "y", "z", "w"]} for i in range(9)],
        },
    })
    assert len(spec["params"]["columns"]) == 3 and len(spec["params"]["rows"]) == 7
    assert all(len(r["cells"]) == 3 for r in spec["params"]["rows"])


def test_describe_figure_gives_focus_keys_for_both_new_templates():
    timeline = sanitize_visual({"template": "timeline", "params": {"events": [{"date": "1", "label": "a"}, {"date": "2", "label": "b"}, {"date": "3", "label": "c"}]}})
    text, keys = describe_figure(timeline)
    assert text and keys == ["0", "1", "2"]

    comparison = sanitize_visual({
        "template": "comparison",
        "params": {"columns": ["A", "B"], "rows": [{"label": "r1", "cells": ["x", "y"]}, {"label": "r2", "cells": ["x", "y"]}]},
    })
    text, keys = describe_figure(comparison)
    assert text and keys == ["0", "1"]


def test_explain_routes_a_versus_topic_to_the_comparison_template(tmp_path):
    """End-to-end through the pipeline against the fake service, since nothing else exercises `comparison`."""
    import asyncio

    from app.ai.gateway import Gateway
    from app.ai.pipeline import explain
    from app.config import Settings

    from ._fake_service import build_fake

    s = Settings(base_url="http://fake-ai.test", api_key="test-key")
    transport, _ = build_fake({s.compact_model: "compact", s.mid_model: "mid"})
    gw = Gateway(s, transport)
    out = asyncio.run(explain(gw, "Renewable vs nonrenewable energy"))
    assert out["visual"]["template"] == "comparison"
    assert all(len(r["cells"]) == len(out["visual"]["params"]["columns"]) for r in out["visual"]["params"]["rows"])
