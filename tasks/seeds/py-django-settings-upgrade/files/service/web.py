"""HTTP-shaped middleware behavior kept independent of framework imports."""


def middleware_names(settings) -> list[str]:
    return [entry.rsplit(".", 1)[-1] for entry in settings.MIDDLEWARE]


def handle_probe(settings, request_id: str) -> dict[str, object]:
    state: dict[str, object] = {"request_id": request_id, "trace": []}
    for name in middleware_names(settings):
        state["trace"].append(name)
        if name == "SecurityHeadersMiddleware":
            state["frame"] = "DENY"
        elif name == "RequestIdMiddleware":
            state["seen_request_id"] = request_id
        elif name == "SessionMiddleware":
            state["session"] = "loaded"
        elif name == "AuditMiddleware":
            state["audit"] = f"{state.get('seen_request_id')}:{state.get('session')}"
    return state

