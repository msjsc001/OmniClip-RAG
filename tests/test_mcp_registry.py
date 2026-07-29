import unittest

from omniclip_rag.mcp.registry import (
    MCPB_ENTRY_POINT,
    MCPB_ICON_NAME,
    REGISTRY_DESCRIPTION,
    REGISTRY_SERVER_NAME,
    REGISTRY_TITLE,
    build_mcpb_manifest,
    build_registry_server_payload,
    mcpb_download_url,
    mcpb_filename,
)


class McpRegistryTests(unittest.TestCase):
    def test_manifest_uses_binary_bundle_entrypoint(self) -> None:
        manifest = build_mcpb_manifest('0.4.1')
        self.assertEqual(manifest['manifest_version'], '0.4')
        self.assertEqual(manifest['name'], REGISTRY_SERVER_NAME)
        self.assertEqual(manifest['display_name'], REGISTRY_TITLE)
        self.assertEqual(manifest['icon'], MCPB_ICON_NAME)
        self.assertEqual(manifest['server']['type'], 'binary')
        self.assertEqual(manifest['server']['entry_point'], MCPB_ENTRY_POINT)
        self.assertEqual(
            manifest['server']['mcp_config']['command'],
            '${__dirname}/server/OmniClipRAG-MCP.exe',
        )
        self.assertEqual(manifest['server']['mcp_config']['args'], [])

    def test_registry_payload_points_at_release_mcpb(self) -> None:
        payload = build_registry_server_payload(file_sha256='abc123', version='0.4.1')
        self.assertEqual(payload['name'], REGISTRY_SERVER_NAME)
        self.assertEqual(payload['title'], REGISTRY_TITLE)
        self.assertEqual(payload['description'], REGISTRY_DESCRIPTION)
        self.assertEqual(payload['version'], '0.4.1')
        self.assertEqual(payload['packages'][0]['registryType'], 'mcpb')
        self.assertEqual(payload['packages'][0]['transport']['type'], 'stdio')
        self.assertEqual(payload['packages'][0]['identifier'], mcpb_download_url('0.4.1'))
        self.assertEqual(payload['packages'][0]['fileSha256'], 'abc123')

    def test_mcpb_filename_contains_mcp_and_version(self) -> None:
        filename = mcpb_filename('0.4.1')
        self.assertIn('mcp', filename)
        self.assertTrue(filename.endswith('-v0.4.1.mcpb'))


if __name__ == '__main__':
    unittest.main()
