# filepath: tests/fixtures/distributions.py
"""Project frozen historical evidence into the current company-action input schema."""

import json
from pathlib import Path


def verified_distributions() -> list[dict[str, object]]:
    """Read business facts while keeping the original evidence file unchanged.

    Example:
        case = verified_distributions()[0]
    """
    cases = json.loads((Path(__file__).parent / "corporate_actions/verified_distributions.json").read_text())
    for case in cases:
        for row in case["records"]:
            row.pop("source_row_id")
        review = case["review"]
        review.pop("source_digest")
        review["related_sources"] = list(review.get("related_sources", {}))
        review["evidence_refs"] = [ref.split("#sha256=", 1)[0] for ref in review["evidence_refs"]]
    return cases
