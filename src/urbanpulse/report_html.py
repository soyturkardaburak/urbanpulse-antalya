from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, StrictUndefined


def render_validation_html(summary: dict) -> str:
    template = (Path(__file__).parent / "templates" / "validation.html.j2").read_text(encoding="utf-8")
    environment = Environment(autoescape=True, undefined=StrictUndefined)
    return environment.from_string(template).render(summary=summary)
