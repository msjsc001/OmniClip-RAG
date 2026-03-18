from __future__ import annotations

__all__ = [
    'MCP_SEARCH_TOOL',
    'MCP_STATUS_TOOL',
    'OmniClipMcpApplication',
    'REGISTRY_SERVER_NAME',
    'REGISTRY_TITLE',
    'build_mcpb_manifest',
    'build_registry_server_payload',
    'mcpb_download_url',
    'mcpb_filename',
]


def __getattr__(name: str):
    if name in {'MCP_SEARCH_TOOL', 'MCP_STATUS_TOOL', 'OmniClipMcpApplication'}:
        from .core import MCP_SEARCH_TOOL, MCP_STATUS_TOOL, OmniClipMcpApplication

        return {
            'MCP_SEARCH_TOOL': MCP_SEARCH_TOOL,
            'MCP_STATUS_TOOL': MCP_STATUS_TOOL,
            'OmniClipMcpApplication': OmniClipMcpApplication,
        }[name]
    if name in {'REGISTRY_SERVER_NAME', 'REGISTRY_TITLE', 'build_mcpb_manifest', 'build_registry_server_payload', 'mcpb_download_url', 'mcpb_filename'}:
        from .registry import (
            REGISTRY_SERVER_NAME,
            REGISTRY_TITLE,
            build_mcpb_manifest,
            build_registry_server_payload,
            mcpb_download_url,
            mcpb_filename,
        )

        return {
            'REGISTRY_SERVER_NAME': REGISTRY_SERVER_NAME,
            'REGISTRY_TITLE': REGISTRY_TITLE,
            'build_mcpb_manifest': build_mcpb_manifest,
            'build_registry_server_payload': build_registry_server_payload,
            'mcpb_download_url': mcpb_download_url,
            'mcpb_filename': mcpb_filename,
        }[name]
    raise AttributeError(name)
