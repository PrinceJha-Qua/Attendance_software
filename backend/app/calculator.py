"""The attendance-requirement calculator (docs/V1_SCOPE_AND_PHASES.md section 3). Exact integer/fraction maths, no floats."""
from fractions import Fraction
from math import ceil, floor


def compute(attended: int, conducted: int, required_pct: float = 75.0, planned_total: int | None = None) -> dict:
    A, T = attended, conducted
    r = Fraction(str(required_pct)) / 100
    S = planned_total if planned_total is not None and planned_total >= T else None  # a plan smaller than what already happened is ignored
    out = {"attended": A, "conducted": T, "required_pct": float(required_pct), "planned_total": S, "pct": None, "status": "none",
           "skip_now": None, "can_skip_until_end": None, "need_in_a_row": None, "reachable": None}
    if S is not None:
        must = max(0, ceil(r * S) - A)
        out["reachable"] = A + (S - T) >= ceil(r * S)
    if T == 0:
        if S is not None:
            out["can_skip_until_end"] = max(0, S - ceil(r * S))
        return out
    out["pct"] = round(100 * A / T, 1)
    if Fraction(A, T) >= r:
        out["skip_now"] = floor(Fraction(A) / r) - T if r > 0 else None
        out["status"] = "safe" if r == 0 or (out["skip_now"] or 0) >= 2 else "borderline"
        if S is not None:
            out["can_skip_until_end"] = max(0, (S - T) - must)
    else:
        out["status"] = "below"
        out["need_in_a_row"] = ceil((r * T - A) / (1 - r)) if r < 1 else None
    return out
