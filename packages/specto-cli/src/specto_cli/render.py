"""Human-readable rendering of command results and errors. ASCII indicators only."""

from __future__ import annotations

import json
from typing import Any

from specto_core.report import ConversionReport

TAG_OK = "[OK]"
TAG_ERROR = "[ERROR]"
TAG_WARN = "[WARN]"
TAG_INFO = "[INFO]"


def render_result(result: Any, *, name: str = "") -> str:
    """A command result as indented ``key: value`` lines; conversion reports as their
    ``[OK]``/``[SKIP]``/... lines first."""
    if not isinstance(result, dict):
        return _scalar(result)
    lines: list[str] = []
    rest = dict(result)
    report = rest.pop("report", None)
    if isinstance(report, dict) and report.get("entries"):
        lines.append(ConversionReport.model_validate(report).text())
    if "patch" in rest and isinstance(rest["patch"], list):
        patch = rest.pop("patch")
        lines.extend(_render_kv(rest))
        if patch:
            lines.append("patch:")
            lines.extend(f"  {op.get('op')} {op.get('path')}" for op in patch)
    else:
        lines.extend(_render_kv(rest))
    return "\n".join(lines)


def _render_kv(d: dict[str, Any], indent: int = 0) -> list[str]:
    pad = "  " * indent
    out: list[str] = []
    for k, v in d.items():
        if isinstance(v, dict):
            if not v:
                out.append(f"{pad}{k}: {{}}")
            elif _flat(v):
                out.append(f"{pad}{k}: " + ", ".join(f"{a}={_scalar(b)}" for a, b in v.items()))
            else:
                out.append(f"{pad}{k}:")
                out.extend(_render_kv(v, indent + 1))
        elif isinstance(v, list):
            if not v:
                out.append(f"{pad}{k}: []")
            elif all(not isinstance(x, dict | list) for x in v):
                out.append(f"{pad}{k}: " + ", ".join(_scalar(x) for x in v))
            else:
                out.append(f"{pad}{k}:")
                for x in v:
                    if isinstance(x, dict):
                        sub = _render_kv(x, indent + 2)
                        if sub:
                            sub[0] = f"{pad}  - " + sub[0].lstrip()
                            out.extend(sub)
                    else:
                        out.append(f"{pad}  - {_scalar(x)}")
        else:
            out.append(f"{pad}{k}: {_scalar(v)}")
    return out


def _flat(d: dict[str, Any]) -> bool:
    return len(d) <= 6 and all(not isinstance(v, dict | list) for v in d.values())


def _scalar(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, dict | list):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_error(code: str, message: str, fix: str | None = None) -> str:
    out = f"{TAG_ERROR} {code}: {message}"
    if fix:
        out += f"\n  -> {fix}"
    return out
