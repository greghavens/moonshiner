"""Composition root: create_app() wires the store, services and router
into a plain WSGI callable."""
from httpio import bad_request, not_found, request_from_environ, to_wsgi
from repo import NotFoundError, Store
from router import Router
from routes import register_all
from services import build_services


def create_app(store=None):
    store = store if store is not None else Store()
    services = build_services(store)
    router = Router()
    register_all(router)

    def application(environ, start_response):
        request = request_from_environ(environ)
        try:
            response = router.dispatch(services, request)
        except NotFoundError as exc:
            response = not_found(str(exc))
        except ValueError as exc:
            response = bad_request(str(exc))
        return to_wsgi(response, start_response)

    return application


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    with make_server("127.0.0.1", 8004, create_app()) as httpd:
        print("workboard dev server on http://127.0.0.1:8004")
        httpd.serve_forever()
