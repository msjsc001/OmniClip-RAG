from __future__ import annotations

from typing import Any

from .. import __version__


REGISTRY_SCHEMA_URL = 'https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json'
REGISTRY_SERVER_NAME = 'io.github.msjsc001/omniclip-rag-mcp'
REGISTRY_TITLE = 'OmniClip RAG'
REGISTRY_DESCRIPTION = 'Read-only local-first MCP server for private Markdown, PDF, and Tika-backed search on Windows.'
REGISTRY_REPOSITORY_URL = 'https://github.com/msjsc001/OmniClip-RAG'
REGISTRY_DOCUMENTATION_URL = 'https://github.com/msjsc001/OmniClip-RAG/blob/main/MCP_SETUP.md'
REGISTRY_SUPPORT_URL = 'https://github.com/msjsc001/OmniClip-RAG/issues'
REGISTRY_RELEASES_URL = 'https://github.com/msjsc001/OmniClip-RAG/releases'
MCPB_MANIFEST_VERSION = '0.4'
MCPB_BUNDLE_PREFIX = 'omniclip-rag-mcp-win-x64'
MCP_EXE_NAME = 'OmniClipRAG-MCP.exe'
MCPB_ENTRY_POINT = f'server/{MCP_EXE_NAME}'
MCPB_ICON_NAME = 'icon.png'


def registry_release_tag(version: str = __version__) -> str:
    return f'v{version}'


def mcpb_filename(version: str = __version__) -> str:
    return f'{MCPB_BUNDLE_PREFIX}-v{version}.mcpb'


def mcpb_download_url(version: str = __version__) -> str:
    return f'{REGISTRY_RELEASES_URL}/download/{registry_release_tag(version)}/{mcpb_filename(version)}'


def build_mcpb_manifest(version: str = __version__) -> dict[str, Any]:
    return {
        'manifest_version': MCPB_MANIFEST_VERSION,
        'name': REGISTRY_SERVER_NAME,
        'display_name': REGISTRY_TITLE,
        'version': version,
        'description': REGISTRY_DESCRIPTION,
        'author': {
            'name': 'msjsc001',
        },
        'repository': {
            'type': 'git',
            'url': REGISTRY_REPOSITORY_URL,
        },
        'homepage': REGISTRY_REPOSITORY_URL,
        'documentation': REGISTRY_DOCUMENTATION_URL,
        'support': REGISTRY_SUPPORT_URL,
        'icon': MCPB_ICON_NAME,
        'license': 'MIT',
        'keywords': [
            'mcp',
            'rag',
            'markdown',
            'pdf',
            'tika',
            'windows',
            'local-first',
            'read-only',
        ],
        'compatibility': {
            'platforms': ['win32'],
        },
        'server': {
            'type': 'binary',
            'entry_point': MCPB_ENTRY_POINT,
            'mcp_config': {
                'command': f'${{__dirname}}/{MCPB_ENTRY_POINT}',
                'args': [],
                'env': {},
            },
        },
        'tools': [
            {
                'name': 'omniclip.status',
                'description': 'Return OmniClip query readiness, runtime status, and the current live snapshot.',
            },
            {
                'name': 'omniclip.search',
                'description': 'Search private Markdown, PDF, and Tika-backed knowledge bases through OmniClip.',
            },
        ],
    }


def build_registry_server_payload(
    *,
    file_sha256: str,
    version: str = __version__,
    identifier: str | None = None,
) -> dict[str, Any]:
    resolved_identifier = (identifier or mcpb_download_url(version)).strip()
    return {
        '$schema': REGISTRY_SCHEMA_URL,
        'name': REGISTRY_SERVER_NAME,
        'title': REGISTRY_TITLE,
        'description': REGISTRY_DESCRIPTION,
        'repository': {
            'url': REGISTRY_REPOSITORY_URL,
            'source': 'github',
        },
        'version': version,
        'packages': [
            {
                'registryType': 'mcpb',
                'identifier': resolved_identifier,
                'fileSha256': file_sha256.strip(),
                'transport': {
                    'type': 'stdio',
                },
            }
        ],
    }
