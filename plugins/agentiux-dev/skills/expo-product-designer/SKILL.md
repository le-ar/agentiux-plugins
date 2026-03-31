---
name: expo-product-designer
description: Use for implementation-ready Expo and React Native UI handoffs inside AgentiUX Dev after the user selects a visual direction. This skill covers Android and iOS deltas, Nativewind-friendly guidance, and deterministic mobile verification hooks.
---

# Expo Product Designer

## Read First

- `../../references/design-workflow.md`
- `../../references/visual-verification.md`
- `../../references/stack-profiles.md`

## Required Workflow

1. Start from the persisted `DesignBrief` and selected `ReferenceBoard`, not from a generic mobile moodboard.
2. Produce a handoff that names screen structure, component inventory, motion rules, typography, color direction, accessibility constraints, and copy tone.
3. Include Android and iOS deltas when they affect layout, navigation, safe areas, or interaction density.
4. Include concrete Expo verification hooks:
   - stable screen names
   - target devices or simulators
   - launch conditions
   - masked dynamic zones
   - expected capture states
5. Keep the handoff compatible with Expo / React Native implementation, including Nativewind when the workspace uses it.

## Guardrails

- Do not collapse Android and iOS into a browser-style single-surface spec.
- Do not leave deterministic capture requirements implicit.
