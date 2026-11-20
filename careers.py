"""The public careers pages (/careers...). Placeholder until the hiring
feature lands: the blueprint exists so the app registers cleanly, and it
has no routes yet. These pages are public: they never read or write the
session."""

from flask import Blueprint

careers_bp = Blueprint("careers", __name__, url_prefix="/careers")
