---
name: mobile-platform
description: Use for React Native, Expo, Nativewind, Android, and iOS workflows inside AgentiUX Dev. This skill covers deterministic emulator and simulator strategy, mobile stage planning, and local-only mobile verification artifacts.
---

# Mobile Platform

## Read First

- `../../references/stack-profiles.md`
- `../../references/visual-verification.md`

## Required Workflow

1. Treat React Native, Expo, Nativewind, Android, and iOS as first-class mobile signals when building or revising the external stage plan.
2. When the task is visual direction, product UI exploration, or implementation-ready Expo design handoff, route to `design-orchestrator` and then `expo-product-designer`.
3. Prefer Detox as the deterministic runner for React Native and Expo flows.
4. Use Compose screenshot testing for Compose-native Android UI where present.
5. Use simulator-first capture and stable device state for iOS-native work.
6. Keep mobile screenshots and traces outside the repo.

## Guardrails

- Do not reduce mobile support to browser-only checks.
- Do not rely on Maestro or EAS as the only deterministic gate.
- Do not forget native Android and iOS paths when the repo contains mixed RN plus native code.
