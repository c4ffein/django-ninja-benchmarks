"""ASGI entrypoint for the Flask app, so it can be served under uvicorn.

Flask is WSGI; asgiref's WsgiToAsgi adapts it to ASGI. Used only by the
server-matrix experiment (all-async parse/validate condition).
"""
from asgiref.wsgi import WsgiToAsgi
from main import app as wsgi_app

application = WsgiToAsgi(wsgi_app)
