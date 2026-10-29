"""Every remaining v1 endpoint from docs/V1_SCOPE_AND_PHASES.md section 4, stubbed with the right
auth rule. Each phase replaces its stubs with real handlers and removes them from this table."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app import config
from app.deps import require_role

router = APIRouter()
S, P = "student", "professor"

# (method, path, roles, tag, phase)
STUBS = [
    # student: enrollment and privacy
    # student: check-in
    # student: dashboard
    # professor: setup
    # professor: session
    # professor: reports and disputes
    # ops
]


def _make_handler(phase: int):
    def handler():
        return JSONResponse(
            {"error": {"code": "NOT_IMPLEMENTED", "message": f"Planned for phase {phase}", "request_id": ""}},
            status_code=501,
        )
    return handler


for method, path, roles, tag, phase in STUBS:
    router.add_api_route(
        path, _make_handler(phase), methods=[method], tags=[tag],
        dependencies=[Depends(require_role(*roles))], summary=f"[stub P{phase}] {method} {path}",
        status_code=501,
    )
