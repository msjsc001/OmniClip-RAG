# OmniClip RAG Website Asset Prompts

## Use This File For

- replacing the placeholder hero artwork,
- replacing the placeholder MCP concept diagram,
- preparing a cleaner social preview image,
- capturing the real product screenshots the website expects.

The website already assumes these file names in `docs/assets/`:

- `hero-illustration.webp`
- `mcp-diagram.webp`
- `gui-shot.webp`
- `mcp-shot.webp`
- `social-preview.webp`

## 1. Hero Illustration

Use this for the main landing-page visual.

```text
A minimalist editorial landing-page illustration for a local-first knowledge retrieval product. Split composition. Left side: a seated person at a desk, calm line-art style, with chaotic loose handwritten lines rising into a soft slate-blue cloud, representing scattered private knowledge and loss of control. Right side: the same person at the desk, with a stable sage-green geometric cube resting on the desk, connected upward by a single thin dusty-purple line to a small glowing star symbol, representing a controlled local knowledge core safely connected to AI. Warm off-white paper-like background, lots of negative space, refined confident continuous linework, flat Morandi palette, no clutter, no gradients, no glossy effects, elegant magazine illustration style, premium and timeless, 16:9.
```

## 2. MCP Concept Diagram

Use this for the website MCP section illustration.

```text
A minimalist conceptual line-art illustration showing a read-only MCP connection. A calm geometric knowledge core on one side, a simple desktop app icon on another, and a clean AI client node on the third side, connected by thin precise lines. Editorial style, warm off-white background, slate-blue outlines, sage-green core, dusty-purple signal line, lots of negative space, flat colors, premium timeless aesthetic, 16:9.
```

## 3. Social Preview Image

Use this if the placeholder social image needs a polished replacement.

```text
A premium minimalist social preview image for an open-source local-first knowledge retrieval project named OmniClip RAG. Warm off-white editorial background, one stable sage-green cube representing a local knowledge core, a thin dusty-purple line rising to a subtle star symbol, slate-blue typography, large negative space, elegant and calm, no futuristic glow, no SaaS gradients, no clutter. Include room for the text: 'OmniClip RAG' and 'Keep your private knowledge local.' 2:1 aspect ratio.
```

## 4. Real Screenshot Capture Checklist

These images should be **real captures from the actual product**, not AI-generated mockups.

### `gui-shot.webp`

Capture a real desktop search surface that clearly shows:

- the query area,
- a visible result list or result detail,
- source labels / result structure,
- enough surrounding chrome to look like a real, working app.

Prefer:

- the main desktop query interface,
- a light background if possible,
- a crop that still breathes and does not feel cramped.

### `mcp-shot.webp`

Preferred target:

- a real **Jan.ai** or **OpenClaw** configuration screen showing OmniClip RAG successfully mounted as an MCP server.

If unavailable, second-best fallback:

- a real OmniClip configuration / runtime / MCP-adjacent management surface that looks stable and production-like.

For the ideal Jan.ai / OpenClaw screenshot, make sure the capture visibly shows:

- the client name,
- `stdio` or MCP configuration context,
- the `OmniClipRAG-MCP.exe` path or a successful connected state,
- enough surrounding UI to make the handshake feel real.

## Export Guidance

- Export website assets as `webp`
- Keep hero and MCP concept assets around `1600x900`
- Keep the social preview around `1280x640`
- Avoid transparent backgrounds; use the warm off-white site background
