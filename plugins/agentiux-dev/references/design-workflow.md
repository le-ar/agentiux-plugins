# Design Workflow

AgentiUX Dev uses a deterministic design workflow for web and Expo surfaces.

## Required Sequence

1. Make sure the workspace is initialized in external state.
2. Persist a `DesignBrief` before collecting references.
3. Use live web and image search to collect 3 to 5 concrete references.
4. Persist a `ReferenceBoard` outside the repo and cache local previews when available.
5. Wait for the user to select a direction or ask for another search pass.
6. Persist a `DesignHandoff` only after the direction is chosen.
7. Include deterministic verification hooks in the handoff.

## Handoff Minimums

- layout system
- component inventory
- motion rules
- typography and colors
- accessibility constraints
- copy tone
- platform deltas
- stable screen IDs or routes
- masked dynamic zones
- capture instructions for web or Expo mobile
