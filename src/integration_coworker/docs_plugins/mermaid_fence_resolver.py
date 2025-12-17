"""MkDocs plugin to resolve mermaid fence format callable at runtime.

MkDocs loads `mkdocs.yml` with `yaml.safe_load`, so python callables are stored as
strings in the config. This plugin imports the callable and replaces the string
so pymdownx.superfences continues to render mermaid blocks using
`fence_code_format`.

This module is intentionally duplicated from `docs/plugins/mermaid_fence_resolver.py`
so the plugin can be installed and discovered via `mkdocs.plugins` entry points.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from mkdocs.plugins import BasePlugin


def _resolve_callable(dotted_path: str):
    module_path, attr = dotted_path.rsplit(".", 1)
    module = import_module(module_path)
    return getattr(module, attr)


class MermaidFenceResolverPlugin(BasePlugin):
    config_scheme: tuple = ()

    def on_config(self, config: dict[str, Any]) -> dict[str, Any]:
        extensions = config.get("markdown_extensions", [])
        for ext in extensions:
            if not isinstance(ext, dict):
                continue

            superfences = ext.get("pymdownx.superfences")
            if not isinstance(superfences, dict):
                continue

            fences = superfences.get("custom_fences", [])
            if not isinstance(fences, list):
                continue

            for fence in fences:
                if not isinstance(fence, dict):
                    continue

                fmt = fence.get("format")
                if isinstance(fmt, str):
                    try:
                        fence["format"] = _resolve_callable(fmt)
                    except Exception as exc:  # pragma: no cover
                        raise ImportError(
                            f"Could not import fence format callable '{fmt}': {exc}"
                        ) from exc

        return config
