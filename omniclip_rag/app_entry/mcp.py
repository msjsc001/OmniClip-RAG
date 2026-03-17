from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..headless.bootstrap import create_headless_context
from ..mcp.core import MCP_SELFTEST_QUERY, OmniClipMcpApplication


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run OmniClip RAG as a read-only MCP server.')
    parser.add_argument('--data-root', default='')
    parser.add_argument('--vault', default='')
    parser.add_argument('--mcp-selfcheck', action='store_true')
    parser.add_argument('--query', default=MCP_SELFTEST_QUERY)
    parser.add_argument('--output', default='')
    return parser.parse_args(argv)


def _write_payload(output_path: str, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    target = str(output_path or '').strip()
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding='utf-8')
        return
    print(serialized)


def run_mcp_selfcheck(*, data_root: str, vault_path: str, query: str, output_path: str) -> int:
    context = create_headless_context(data_root=data_root, vault_path=vault_path)
    try:
        app = OmniClipMcpApplication(context)
        payload = app.selfcheck_payload(query=str(query or '').strip() or MCP_SELFTEST_QUERY)
        _write_payload(output_path, payload)
        return 0 if payload.get('ok') else 1
    finally:
        context.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if bool(args.mcp_selfcheck):
        return run_mcp_selfcheck(
            data_root=args.data_root,
            vault_path=args.vault,
            query=args.query,
            output_path=args.output,
        )
    context = create_headless_context(data_root=args.data_root, vault_path=args.vault)
    try:
        app = OmniClipMcpApplication(context)
        app.run_stdio()
        return 0
    finally:
        context.close()
