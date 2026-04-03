import {
  buildArtifactPath,
  compareExpectedMap,
  createReportContext,
  ensureTarget,
  finalizeReport,
  recordReachabilityPath,
  recordCheck,
  runHook,
  writeReport,
} from "../core/index.js";

function resolveMatcher(by, locator) {
  if (!locator?.kind || locator.value == null) {
    return null;
  }
  switch (locator.kind) {
    case "test_id":
    case "semantics_tag":
      return by.id(String(locator.value));
    case "text":
      return by.text(String(locator.value));
    default:
      return null;
  }
}

function targetSpecById(spec, targetId) {
  return (spec.targets ?? []).find((candidate) => candidate.target_id === targetId) ?? null;
}

function resolveMatcherFromTargetSpec(by, spec, targetId) {
  if (!targetId) {
    return null;
  }
  const targetSpec = targetSpecById(spec, targetId);
  return targetSpec ? resolveMatcher(by, targetSpec.locator) : null;
}

function resolveStepMatcher(by, spec, pathSpec, step, key = "target_id") {
  const directMatcher = resolveMatcher(by, step.locator);
  if (directMatcher) {
    return directMatcher;
  }
  const targetId = step[key] ?? pathSpec.target_id ?? null;
  return resolveMatcherFromTargetSpec(by, spec, targetId);
}

async function readAttributes(targetElement) {
  try {
    return (await targetElement.getAttributes()) ?? {};
  } catch {
    return {};
  }
}

async function runProbe(spec, options, targetSpec, attributes) {
  if (typeof options?.probe === "function") {
    return (await options.probe(targetSpec.target_id, { spec, target: targetSpec, attributes })) ?? {};
  }
  if (spec.platform_hooks?.module) {
    return (await runHook(spec.platform_hooks.module, spec.platform_hooks.function, targetSpec.target_id, {
      spec,
      target: targetSpec,
      attributes,
    })) ?? {};
  }
  return {};
}

async function attemptScroll(elementFactory, targetElement, scrollLocator, by, expectFn) {
  if (!scrollLocator) {
    return false;
  }
  const matcher = resolveMatcher(by, scrollLocator);
  if (!matcher) {
    return false;
  }
  const scrollElement = elementFactory(matcher);
  for (let index = 0; index < 5; index += 1) {
    try {
      await scrollElement.scroll(180, "down");
    } catch {
      break;
    }
    try {
      await expectFn(targetElement).toBeVisible();
      return true;
    } catch {
      continue;
    }
  }
  return false;
}

async function ensureVisibleWithScroll(elementFactory, targetElement, scrollLocator, by, expectFn) {
  try {
    await expectFn(targetElement).toBeVisible();
    return true;
  } catch {
    return attemptScroll(elementFactory, targetElement, scrollLocator, by, expectFn);
  }
}

async function executeReachabilityStep(runtime, spec, pathSpec, step) {
  const { element, by, expect: detoxExpect = global.expect, waitFor: detoxWaitFor = global.waitFor } = runtime;
  const timeout = Number(step.timeout_ms ?? 3000);
  if (step.action === "wait_for") {
    const matcher = resolveStepMatcher(by, spec, pathSpec, step);
    if (matcher) {
      const targetElement = element(matcher);
      if (typeof detoxWaitFor === "function") {
        await detoxWaitFor(targetElement).toBeVisible().withTimeout(timeout);
      } else {
        await detoxExpect(targetElement).toBeVisible();
      }
    } else {
      await new Promise((resolve) => setTimeout(resolve, timeout));
    }
    return { action: step.action, target_id: step.target_id ?? pathSpec.target_id ?? null };
  }
  const matcher = resolveStepMatcher(by, spec, pathSpec, step);
  if (!matcher) {
    throw new Error(`Reachability step ${step.action} could not resolve a matcher`);
  }
  const targetElement = element(matcher);
  if (step.action === "ensure_visible" || step.action === "scroll_to") {
    const targetId = step.target_id ?? pathSpec.target_id ?? null;
    const targetSpec = targetId ? targetSpecById(spec, targetId) : null;
    const scrollLocator = step.scroll_container_locator ?? targetSpec?.scroll_container_locator ?? null;
    const visible = await ensureVisibleWithScroll(element, targetElement, scrollLocator, by, detoxExpect);
    if (!visible) {
      throw new Error(`Reachability step ${step.action} could not make target visible`);
    }
    return { action: step.action, target_id: targetId };
  }
  if (step.action === "tap") {
    await targetElement.tap();
    return { action: step.action, target_id: step.target_id ?? pathSpec.target_id ?? null };
  }
  if (step.action === "long_press") {
    if (typeof targetElement.longPress === "function") {
      await targetElement.longPress(Number(step.duration_ms ?? 1000));
    } else {
      await targetElement.tap();
    }
    return { action: step.action, target_id: step.target_id ?? pathSpec.target_id ?? null };
  }
  if (step.action === "swipe") {
    await targetElement.swipe(String(step.direction ?? "left"), "fast", 0.75);
    return { action: step.action, target_id: step.target_id ?? pathSpec.target_id ?? null, direction: step.direction ?? "left" };
  }
  if (step.action === "drag") {
    if (typeof targetElement.drag !== "function") {
      throw new Error("Detox runtime does not expose drag on the resolved element");
    }
    await targetElement.drag(Number(step.distance_px ?? 120), String(step.direction ?? "down"), 0.5, 0.5);
    return {
      action: step.action,
      from_target_id: step.from_target_id ?? step.target_id ?? pathSpec.target_id ?? null,
      to_target_id: step.to_target_id ?? null,
    };
  }
  if (step.action === "type_text") {
    const textValue = String(step.value ?? step.text ?? "");
    if (typeof targetElement.replaceText === "function") {
      await targetElement.replaceText(textValue);
    } else {
      await targetElement.typeText(textValue);
    }
    return {
      action: step.action,
      target_id: step.target_id ?? pathSpec.target_id ?? null,
      value_length: textValue.length,
    };
  }
  throw new Error(`Unsupported reachability step action ${step.action}`);
}

async function executeReachabilityPaths(runtime, spec, report) {
  for (const pathSpec of spec.reachability_paths ?? []) {
    const executed_steps = [];
    try {
      for (const step of pathSpec.steps ?? []) {
        executed_steps.push(await executeReachabilityStep(runtime, spec, pathSpec, step));
      }
      recordReachabilityPath(report, {
        path_id: pathSpec.path_id,
        title: pathSpec.title,
        status: "passed",
        target_id: pathSpec.target_id ?? null,
        executed_steps,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      recordReachabilityPath(report, {
        path_id: pathSpec.path_id,
        title: pathSpec.title,
        status: "failed",
        target_id: pathSpec.target_id ?? null,
        executed_steps,
        message,
      });
      throw new Error(`Reachability path ${pathSpec.path_id} failed: ${message}`);
    }
  }
}

export async function runSemanticChecks(runtime, rawSpec, options = {}) {
  const { device, element, by, expect: detoxExpect = global.expect } = runtime;
  const { spec, report } = createReportContext("detox-visual", rawSpec);
  try {
    await executeReachabilityPaths(runtime, spec, report);
    for (const targetSpec of spec.targets) {
      const matcher = resolveMatcher(by, targetSpec.locator);
      ensureTarget(report, targetSpec.target_id);
      if (!matcher) {
        recordCheck(report, targetSpec.target_id, {
          check_id: "presence_uniqueness",
          status: "failed",
          diagnostics: { reason: "unsupported_locator", locator: targetSpec.locator },
        });
        continue;
      }
      const targetElement = element(matcher);
      let exists = false;
      try {
        await detoxExpect(targetElement).toExist();
        exists = true;
      } catch {
        exists = false;
      }
      const attributes = exists ? await readAttributes(targetElement) : {};
      const probe = exists ? await runProbe(spec, options, targetSpec, attributes) : {};
      recordCheck(report, targetSpec.target_id, {
        check_id: "presence_uniqueness",
        status: exists ? "passed" : "failed",
        diagnostics: { exists, attributes },
      });
      if (!exists) {
        continue;
      }
      let visible = false;
      try {
        await detoxExpect(targetElement).toBeVisible();
        visible = true;
      } catch {
        visible = false;
      }
      recordCheck(report, targetSpec.target_id, {
        check_id: "visibility",
        status: visible ? "passed" : "failed",
        diagnostics: { visible, attributes },
      });
      const becameVisible = visible || (await attemptScroll(element, targetElement, targetSpec.scroll_container_locator, by, detoxExpect));
      recordCheck(report, targetSpec.target_id, {
        check_id: "scroll_reachability",
        status: becameVisible ? "passed" : "failed",
        diagnostics: { visible, becameVisible },
      });
      const attributeMismatches = compareExpectedMap(
        {
          enabled: attributes.enabled,
          focused: attributes.focused,
          label: attributes.label,
          value: attributes.value,
          visible: attributes.visible,
          ...probe.accessibility,
        },
        targetSpec.expected_attributes
      );
      recordCheck(report, targetSpec.target_id, {
        check_id: "accessibility_state",
        status: attributeMismatches.length === 0 ? "passed" : "failed",
        diagnostics: { attributes, probe, mismatches: attributeMismatches },
      });
      const styleMismatches = compareExpectedMap(
        {
          alpha: attributes.alpha,
          elevation: attributes.elevation,
          textSize: attributes.textSize,
          ...probe.style_tokens,
        },
        targetSpec.expected_styles
      );
      recordCheck(report, targetSpec.target_id, {
        check_id: "computed_styles",
        status: styleMismatches.length === 0 ? "passed" : "failed",
        diagnostics: { attributes, probe, mismatches: styleMismatches },
      });
      recordCheck(report, targetSpec.target_id, {
        check_id: "overflow_clipping",
        status: targetSpec.allow_clipping || probe.clipping?.clipped !== true ? "passed" : "failed",
        diagnostics: { allow_clipping: targetSpec.allow_clipping, clipping: probe.clipping ?? {} },
      });
      recordCheck(report, targetSpec.target_id, {
        check_id: "occlusion",
        status: targetSpec.allow_occlusion || probe.metadata?.occluded !== true ? "passed" : "failed",
        diagnostics: { allow_occlusion: targetSpec.allow_occlusion, metadata: probe.metadata ?? {} },
      });
      recordCheck(report, targetSpec.target_id, {
        check_id: "interaction_states",
        status: "passed",
        diagnostics: {
          enabled: attributes.enabled,
          focused: attributes.focused,
          requested_interactions: targetSpec.interactions ?? [],
        },
      });
      recordCheck(report, targetSpec.target_id, {
        check_id: "layout_relations",
        status: "passed",
        diagnostics: { layout: probe.layout ?? {}, expected_layout: targetSpec.expected_layout ?? {} },
      });
      recordCheck(report, targetSpec.target_id, {
        check_id: "text_overflow",
        status: targetSpec.allow_text_truncation || probe.text_overflow?.truncated !== true ? "passed" : "failed",
        diagnostics: { allow_text_truncation: targetSpec.allow_text_truncation, text_overflow: probe.text_overflow ?? {} },
      });
      if (spec.artifacts?.target_screenshots) {
        let screenshotPath = null;
        try {
          screenshotPath =
            (typeof targetElement.takeScreenshot === "function" && (await targetElement.takeScreenshot(targetSpec.target_id))) ||
            (device && typeof device.takeScreenshot === "function" && (await device.takeScreenshot(targetSpec.target_id)));
        } catch {
          screenshotPath = null;
        }
        const artifactPath = screenshotPath || buildArtifactPath(spec, targetSpec.target_id, "target", "png");
        ensureTarget(report, targetSpec.target_id).artifact_paths.push(artifactPath);
      }
    }
    if (spec.auto_scan && typeof options?.autoScan === "function") {
      const autoScanDiagnostics = (await options.autoScan(spec)) ?? {};
      recordCheck(report, "_auto_scan", {
        check_id: "visibility",
        status: (autoScanDiagnostics.visibilityIssues ?? []).length === 0 ? "passed" : "failed",
        diagnostics: autoScanDiagnostics,
      });
    }
  } finally {
    finalizeReport(report, spec);
    await writeReport(spec, report);
  }
  return report;
}
