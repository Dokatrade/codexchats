from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.titles import enrich_search_result_row, enrich_session_row


def build_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def home(
        request: Request,
        q: str | None = None,
        source_id: str | None = None,
        user_name: str | None = None,
        hide_service: str = "1",
        ofac: str = "1",
    ) -> HTMLResponse:
        repo = request.app.state.repo
        source_id_value = int(source_id) if (source_id or "").strip().isdigit() else None
        hide_service_enabled = str(hide_service) != "0"
        ofac_enabled = str(ofac) == "1"
        sessions = repo.list_sessions(
            q=q,
            source_id=source_id_value,
            user_name=user_name,
            limit=100,
        )
        sessions = [enrich_session_row(s) for s in sessions]
        stats = repo.get_dashboard_stats()
        sources = repo.get_sources()
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "sessions": sessions,
                "stats": stats,
                "sources": sources,
                "q": q or "",
                "source_id": source_id_value,
                "user_name": user_name or "",
                "hide_service": hide_service_enabled,
                "ofac": ofac_enabled,
            },
        )

    @router.get("/sessions/{session_id}", response_class=HTMLResponse)
    async def session_detail(
        request: Request,
        session_id: int,
        show_reasoning: int = 0,
        show_service: int = 0,
        ofac: int = 0,
    ) -> HTMLResponse:
        repo = request.app.state.repo
        session = repo.get_session(session_id)
        if session is None:
            return templates.TemplateResponse(
                "not_found.html",
                {"request": request, "message": f"Session {session_id} not found"},
                status_code=404,
            )
        session_view = enrich_session_row(session)
        include_service = bool(show_service or show_reasoning)
        ofac_enabled = bool(ofac)
        messages = repo.get_messages_for_session(
            session_id,
            include_service=include_service,
            ofac=ofac_enabled,
        )
        reasoning_count = repo.count_reasoning_for_session(session_id)
        service_count = repo.count_service_for_session(session_id)
        non_ofac_count = repo.count_non_ofac_for_session(session_id)
        return templates.TemplateResponse(
            "session_detail.html",
            {
                "request": request,
                "session": session_view,
                "messages": messages,
                "show_reasoning": include_service,
                "show_service": include_service,
                "ofac": ofac_enabled,
                "reasoning_count": reasoning_count,
                "service_count": service_count,
                "non_ofac_count": non_ofac_count,
            },
        )

    @router.get("/search", response_class=HTMLResponse)
    async def search(
        request: Request,
        q: str = "",
        show_reasoning: int = 0,
        show_service: int = 0,
    ) -> HTMLResponse:
        repo = request.app.state.repo
        include_service = bool(show_service or show_reasoning)
        results = (
            [
                enrich_search_result_row(r)
                for r in repo.search_messages(
                    q,
                    include_service=include_service,
                )
            ]
            if q.strip()
            else []
        )
        return templates.TemplateResponse(
            "search.html",
            {
                "request": request,
                "q": q,
                "results": results,
                "show_reasoning": include_service,
                "show_service": include_service,
            },
        )

    @router.get("/admin/sources", response_class=HTMLResponse)
    async def admin_sources(request: Request) -> HTMLResponse:
        repo = request.app.state.repo
        sources = repo.get_sources()
        return templates.TemplateResponse(
            "sources.html",
            {"request": request, "sources": sources},
        )

    @router.get("/admin/scans", response_class=HTMLResponse)
    async def admin_scans(request: Request) -> HTMLResponse:
        repo = request.app.state.repo
        runs = repo.list_scan_runs()
        errors = repo.list_parse_errors()
        return templates.TemplateResponse(
            "scans.html",
            {"request": request, "runs": runs, "errors": errors},
        )

    @router.post("/admin/rescan")
    async def admin_rescan(request: Request, mode: str = Form(...)) -> RedirectResponse:
        indexer = request.app.state.indexer
        indexer.run_scan(mode=mode)
        return RedirectResponse(url="/admin/scans", status_code=303)

    @router.get("/health")
    async def health(request: Request) -> JSONResponse:
        repo = request.app.state.repo
        return JSONResponse(
            {
                "status": "ok",
                "stats": repo.get_dashboard_stats(),
                "last_scan_status": repo.get_app_state("last_scan_status"),
                "last_scan_mode": repo.get_app_state("last_scan_mode"),
            }
        )

    @router.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> RedirectResponse:
        return RedirectResponse(url="/static/favicon.svg", status_code=307)

    return router
