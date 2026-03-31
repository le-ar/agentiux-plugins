import fs from "node:fs/promises";
import path from "node:path";

import {
  buildArtifactPath,
  compareExpectedMap,
  createReportContext,
  ensureTarget,
  finalizeReport,
  recordCheck,
  rectIntersects,
  runHook,
  toPlainRect,
  writeReport,
} from "../core/index.js";

function resolveLocator(page, locator) {
  if (!locator?.kind || locator.value == null) {
    return null;
  }
  switch (locator.kind) {
    case "selector":
      return page.locator(String(locator.value));
    case "role":
      return page.getByRole(String(locator.value), {
        exact: Boolean(locator.exact),
        name: locator.name ?? undefined,
      });
    case "test_id":
      return page.getByTestId(String(locator.value));
    case "text":
      return page.getByText(String(locator.value), { exact: Boolean(locator.exact) });
    default:
      return null;
  }
}

async function snapshotTarget(page, targetSpec) {
  const locator = resolveLocator(page, targetSpec.locator);
  if (!locator) {
    return { count: 0, locator: null, rect: null, style: {}, attributes: {}, textOverflow: {}, diagnostics: { reason: "unresolved_locator" } };
  }
  const count = await locator.count();
  if (count === 0) {
    return { count, locator, rect: null, style: {}, attributes: {}, textOverflow: {}, diagnostics: { reason: "missing" } };
  }
  const target = locator.first();
  await target.scrollIntoViewIfNeeded().catch(() => {});
  const rect = toPlainRect(await target.boundingBox().catch(() => null));
  const bundle = await target.evaluate((element) => {
    const computedStyle = window.getComputedStyle(element);
    const role = element.getAttribute("role");
    const ariaChecked = element.getAttribute("aria-checked");
    const ariaDisabled = element.getAttribute("aria-disabled");
    const ariaExpanded = element.getAttribute("aria-expanded");
    const ariaPressed = element.getAttribute("aria-pressed");
    const textContent = element.textContent ?? "";
    return {
      style: {
        color: computedStyle.color,
        backgroundColor: computedStyle.backgroundColor,
        fontSize: computedStyle.fontSize,
        fontWeight: computedStyle.fontWeight,
        opacity: computedStyle.opacity,
        display: computedStyle.display,
        visibility: computedStyle.visibility,
      },
      attributes: {
        role,
        checked: ariaChecked,
        disabled: ariaDisabled === "true" || element.hasAttribute("disabled"),
        expanded: ariaExpanded,
        pressed: ariaPressed,
        text: textContent.trim(),
      },
      textOverflow: {
        horizontal: element.scrollWidth > element.clientWidth,
        vertical: element.scrollHeight > element.clientHeight,
        scrollWidth: element.scrollWidth,
        clientWidth: element.clientWidth,
        scrollHeight: element.scrollHeight,
        clientHeight: element.clientHeight,
      },
    };
  });
  return {
    count,
    locator,
    target,
    rect,
    style: bundle.style,
    attributes: bundle.attributes,
    textOverflow: bundle.textOverflow,
    diagnostics: { count, rect },
  };
}

async function checkVisibility(page, target, snapshot) {
  const visible = await target.isVisible().catch(() => false);
  const viewport = page.viewportSize();
  const viewportRect = viewport
    ? { left: 0, top: 0, right: viewport.width, bottom: viewport.height, width: viewport.width, height: viewport.height }
    : null;
  const inViewport = snapshot.rect ? rectIntersects(snapshot.rect, viewportRect) : false;
  return {
    status: visible && inViewport ? "passed" : "failed",
    diagnostics: { visible, inViewport, rect: snapshot.rect, viewport: viewportRect },
  };
}

async function checkOverflow(page, targetSpec, snapshot) {
  if (!snapshot.target || !snapshot.rect) {
    return { status: "skipped", diagnostics: { reason: "missing_target" } };
  }
  if (targetSpec.allow_clipping) {
    return { status: "passed", diagnostics: { allowed: true } };
  }
  const containerLocator = resolveLocator(page, targetSpec.container_locator);
  const containerRect = containerLocator ? toPlainRect(await containerLocator.first().boundingBox().catch(() => null)) : null;
  if (!containerRect) {
    return { status: "passed", diagnostics: { reason: "no_container_locator", rect: snapshot.rect } };
  }
  const clipped =
    snapshot.rect.left < containerRect.left ||
    snapshot.rect.top < containerRect.top ||
    snapshot.rect.right > containerRect.right ||
    snapshot.rect.bottom > containerRect.bottom;
  return {
    status: clipped ? "failed" : "passed",
    diagnostics: { clipped, rect: snapshot.rect, containerRect },
  };
}

async function checkOcclusion(page, targetSpec, snapshot) {
  if (!snapshot.target || !snapshot.rect) {
    return { status: "skipped", diagnostics: { reason: "missing_target" } };
  }
  if (targetSpec.allow_occlusion) {
    return { status: "passed", diagnostics: { allowed: true } };
  }
  const point = {
    x: snapshot.rect.left + snapshot.rect.width / 2,
    y: snapshot.rect.top + snapshot.rect.height / 2,
  };
  const result = await snapshot.target.evaluate((element, hitPoint) => {
    const occupant = document.elementFromPoint(hitPoint.x, hitPoint.y);
    return {
      occupiedBySelf: occupant === element || element.contains(occupant),
      occupantHtml: occupant?.outerHTML?.slice(0, 200) ?? null,
    };
  }, point);
  return {
    status: result.occupiedBySelf ? "passed" : "failed",
    diagnostics: { point, ...result },
  };
}

async function checkInteractions(targetSpec, snapshot) {
  if (!snapshot.target) {
    return { status: "skipped", diagnostics: { reason: "missing_target" } };
  }
  const executed = [];
  for (const interaction of targetSpec.interactions ?? []) {
    if (interaction === "hover") {
      await snapshot.target.hover().catch(() => {});
      executed.push("hover");
    } else if (interaction === "focus") {
      await snapshot.target.focus().catch(() => {});
      executed.push("focus");
    } else if (interaction === "press") {
      await snapshot.target.press("Enter").catch(() => {});
      executed.push("press");
    }
  }
  const focused = await snapshot.target.evaluate((element) => element === document.activeElement).catch(() => false);
  return {
    status: "passed",
    diagnostics: { executed, focused },
  };
}

async function checkLayout(page, targetSpec, snapshot) {
  if (!snapshot.rect) {
    return { status: "skipped", diagnostics: { reason: "missing_target" } };
  }
  const layout = targetSpec.expected_layout ?? {};
  const diagnostics = { rect: snapshot.rect, assertions: [] };
  let failed = false;
  for (const [key, expectedValue] of Object.entries(layout)) {
    if (["min_width", "min_height", "max_width", "max_height"].includes(key)) {
      const actualKey = key.replace(/^min_|^max_/, "");
      const actualValue = snapshot.rect[actualKey];
      const passed =
        (key.startsWith("min_") && actualValue >= Number(expectedValue)) ||
        (key.startsWith("max_") && actualValue <= Number(expectedValue));
      diagnostics.assertions.push({ key, expected: expectedValue, actual: actualValue, passed });
      failed ||= !passed;
    } else if (key === "container_visible") {
      const containerLocator = resolveLocator(page, targetSpec.container_locator);
      const containerVisible = containerLocator ? await containerLocator.first().isVisible().catch(() => false) : false;
      diagnostics.assertions.push({ key, expected: expectedValue, actual: containerVisible, passed: containerVisible === Boolean(expectedValue) });
      failed ||= containerVisible !== Boolean(expectedValue);
    }
  }
  return { status: failed ? "failed" : "passed", diagnostics };
}

async function runAutoScan(page, spec, report) {
  if (!spec.auto_scan || spec.heuristics.length === 0) {
    return;
  }
  const diagnostics = {};
  if (spec.heuristics.includes("interactive_visibility_scan")) {
    diagnostics.visibility = await page.locator("a,button,[role='button'],input,select,textarea").evaluateAll((elements) =>
      elements
        .map((element) => ({
          tag: element.tagName.toLowerCase(),
          text: (element.textContent ?? "").trim().slice(0, 120),
          width: element.getBoundingClientRect().width,
          height: element.getBoundingClientRect().height,
        }))
        .filter((item) => item.width === 0 || item.height === 0)
    );
    recordCheck(report, "_auto_scan", {
      check_id: "visibility",
      status: diagnostics.visibility.length === 0 ? "passed" : "failed",
      diagnostics: { issues: diagnostics.visibility },
    });
  }
  if (spec.heuristics.includes("interactive_overflow_scan")) {
    diagnostics.overflow = await page.locator("a,button,[role='button']").evaluateAll((elements) =>
      elements
        .map((element) => ({
          text: (element.textContent ?? "").trim().slice(0, 120),
          overflowX: element.scrollWidth > element.clientWidth,
          overflowY: element.scrollHeight > element.clientHeight,
        }))
        .filter((item) => item.overflowX || item.overflowY)
    );
    recordCheck(report, "_auto_scan", {
      check_id: "overflow_clipping",
      status: diagnostics.overflow.length === 0 ? "passed" : "failed",
      diagnostics: { issues: diagnostics.overflow },
    });
  }
  if (spec.heuristics.includes("interactive_occlusion_scan")) {
    diagnostics.occlusion = await page.locator("button,[role='button']").evaluateAll((elements) =>
      elements
        .map((element) => {
          const rect = element.getBoundingClientRect();
          const point = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
          const occupant = document.elementFromPoint(point.x, point.y);
          return {
            text: (element.textContent ?? "").trim().slice(0, 120),
            occluded: occupant !== element && !element.contains(occupant),
          };
        })
        .filter((item) => item.occluded)
    );
    recordCheck(report, "_auto_scan", {
      check_id: "occlusion",
      status: diagnostics.occlusion.length === 0 ? "passed" : "failed",
      diagnostics: { issues: diagnostics.occlusion },
    });
  }
}

export async function runSemanticChecks(page, rawSpec) {
  const { spec, report } = createReportContext("playwright-visual", rawSpec);
  try {
    if (spec.platform_hooks?.module) {
      const hookResult = await runHook(spec.platform_hooks.module, spec.platform_hooks.function, page, spec);
      if (hookResult && typeof hookResult === "object") {
        report.hook_result = hookResult;
      }
    }
    for (const targetSpec of spec.targets) {
      const snapshot = await snapshotTarget(page, targetSpec);
      ensureTarget(report, targetSpec.target_id, snapshot.diagnostics);
      recordCheck(report, targetSpec.target_id, {
        check_id: "presence_uniqueness",
        status: snapshot.count === 1 ? "passed" : "failed",
        diagnostics: { count: snapshot.count },
      });
      if (snapshot.count !== 1) {
        continue;
      }
      const visibility = await checkVisibility(page, targetSpec.target, snapshot);
      recordCheck(report, targetSpec.target_id, { check_id: "visibility", ...visibility });
      recordCheck(report, targetSpec.target_id, {
        check_id: "scroll_reachability",
        status: visibility.status,
        diagnostics: visibility.diagnostics,
      });
      const overflow = await checkOverflow(page, targetSpec, snapshot);
      recordCheck(report, targetSpec.target_id, { check_id: "overflow_clipping", ...overflow });
      const occlusion = await checkOcclusion(page, targetSpec, snapshot);
      recordCheck(report, targetSpec.target_id, { check_id: "occlusion", ...occlusion });
      const interaction = await checkInteractions(targetSpec, snapshot);
      recordCheck(report, targetSpec.target_id, { check_id: "interaction_states", ...interaction });
      const styleMismatches = compareExpectedMap(snapshot.style, targetSpec.expected_styles);
      recordCheck(report, targetSpec.target_id, {
        check_id: "computed_styles",
        status: styleMismatches.length === 0 ? "passed" : "failed",
        diagnostics: { actual: snapshot.style, expected: targetSpec.expected_styles, mismatches: styleMismatches },
      });
      const attributeMismatches = compareExpectedMap(snapshot.attributes, targetSpec.expected_attributes);
      recordCheck(report, targetSpec.target_id, {
        check_id: "accessibility_state",
        status: attributeMismatches.length === 0 ? "passed" : "failed",
        diagnostics: { actual: snapshot.attributes, expected: targetSpec.expected_attributes, mismatches: attributeMismatches },
      });
      const layout = await checkLayout(page, targetSpec, snapshot);
      recordCheck(report, targetSpec.target_id, { check_id: "layout_relations", ...layout });
      const overflowAllowed = targetSpec.allow_text_truncation;
      recordCheck(report, targetSpec.target_id, {
        check_id: "text_overflow",
        status: overflowAllowed || (!snapshot.textOverflow.horizontal && !snapshot.textOverflow.vertical) ? "passed" : "failed",
        diagnostics: snapshot.textOverflow,
      });
      if (spec.artifacts?.target_screenshots) {
        const screenshotPath = buildArtifactPath(spec, targetSpec.target_id, "target", "png");
        await fs.mkdir(path.dirname(screenshotPath), { recursive: true }).catch(() => {});
        await snapshot.target.screenshot({ path: screenshotPath }).catch(() => {});
        const targetEntry = ensureTarget(report, targetSpec.target_id);
        if (!targetEntry.artifact_paths.includes(screenshotPath)) {
          targetEntry.artifact_paths.push(screenshotPath);
        }
      }
    }
    await runAutoScan(page, spec, report);
    if (spec.artifacts?.debug_snapshots) {
      const snapshotPath = buildArtifactPath(spec, "_global", "dom-snapshot", "html");
      await fs.writeFile(snapshotPath, await page.content(), "utf8");
      ensureTarget(report, "_global").artifact_paths.push(snapshotPath);
    }
  } finally {
    finalizeReport(report, spec);
    await writeReport(spec, report);
  }
  return report;
}
