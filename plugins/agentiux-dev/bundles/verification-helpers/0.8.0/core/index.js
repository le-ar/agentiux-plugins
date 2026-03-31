import fs from "node:fs/promises";
import path from "node:path";

export const HELPER_BUNDLE_VERSION = "0.8.0";
export const CHECK_FAMILIES = [
  "presence_uniqueness",
  "visibility",
  "scroll_reachability",
  "overflow_clipping",
  "occlusion",
  "interaction_states",
  "computed_styles",
  "layout_relations",
  "text_overflow",
  "accessibility_state",
  "screenshot_baseline",
];
export const RESULT_STATUSES = new Set([
  "passed",
  "failed",
  "warning",
  "skipped",
  "not_applicable",
  "unknown",
]);
export const DEFAULT_HEURISTICS = [
  "interactive_visibility_scan",
  "interactive_overflow_scan",
  "interactive_occlusion_scan",
];

function clone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

export function normalizeStatus(value) {
  const status = String(value ?? "unknown").trim().toLowerCase() || "unknown";
  return RESULT_STATUSES.has(status) ? status : "unknown";
}

export function normalizeSpec(spec = {}) {
  const requiredChecks = [];
  for (const item of spec.required_checks ?? []) {
    const checkId = String(item ?? "").trim().toLowerCase();
    if (checkId && CHECK_FAMILIES.includes(checkId) && !requiredChecks.includes(checkId)) {
      requiredChecks.push(checkId);
    }
  }
  const heuristics = [];
  for (const item of spec.heuristics ?? []) {
    const heuristic = String(item ?? "").trim().toLowerCase();
    if (heuristic && !heuristics.includes(heuristic)) {
      heuristics.push(heuristic);
    }
  }
  const targets = [];
  for (const rawTarget of spec.targets ?? []) {
    if (!rawTarget || typeof rawTarget !== "object") {
      continue;
    }
    const targetId = String(rawTarget.target_id ?? rawTarget.id ?? "").trim();
    if (!targetId) {
      continue;
    }
    targets.push({
      target_id: targetId,
      locator: clone(rawTarget.locator),
      container_locator: clone(rawTarget.container_locator),
      scroll_container_locator: clone(rawTarget.scroll_container_locator),
      interactions: [...new Set((rawTarget.interactions ?? []).map((value) => String(value).trim().toLowerCase()).filter(Boolean))],
      expected_attributes: clone(rawTarget.expected_attributes) ?? {},
      expected_styles: clone(rawTarget.expected_styles) ?? {},
      expected_layout: clone(rawTarget.expected_layout) ?? {},
      allow_clipping: Boolean(rawTarget.allow_clipping),
      allow_occlusion: Boolean(rawTarget.allow_occlusion),
      allow_text_truncation: Boolean(rawTarget.allow_text_truncation),
    });
  }
  return {
    schema_version: Number(spec.schema_version ?? 2),
    helper_bundle_version: String(spec.helper_bundle_version ?? HELPER_BUNDLE_VERSION),
    runner: String(spec.runner ?? "unknown"),
    case_id: String(spec.case_id ?? "unknown-case"),
    report_path: String(spec.report_path ?? process.env.VERIFICATION_SEMANTIC_REPORT_PATH ?? "semantic-report.json"),
    required_checks: requiredChecks,
    auto_scan: Boolean(spec.auto_scan),
    heuristics: heuristics.length > 0 ? heuristics : (spec.auto_scan ? [...DEFAULT_HEURISTICS] : []),
    targets,
    artifacts: clone(spec.artifacts) ?? {},
    platform_hooks: clone(spec.platform_hooks) ?? {},
    runner_capabilities: clone(spec.runner_capabilities) ?? {},
    locale: spec.locale ?? null,
    timezone: spec.timezone ?? null,
    color_scheme: spec.color_scheme ?? null,
    freeze_clock: Boolean(spec.freeze_clock),
    masks: Array.isArray(spec.masks) ? [...spec.masks] : [],
    target: clone(spec.target) ?? {},
  };
}

export function createReportContext(runner, rawSpec) {
  const spec = normalizeSpec(rawSpec);
  const report = {
    schema_version: 2,
    helper_bundle_version: HELPER_BUNDLE_VERSION,
    runner,
    case_id: spec.case_id,
    generated_at: new Date().toISOString(),
    report_path: spec.report_path,
    targets: [],
    summary: {
      status: "unknown",
      required_checks: [...spec.required_checks],
      check_counts: {
        passed: 0,
        failed: 0,
        warning: 0,
        skipped: 0,
        not_applicable: 0,
        unknown: 0,
      },
      target_count: spec.targets.length,
      failed_checks: [],
      optional_failed_checks: [],
    },
  };
  return { spec, report };
}

export function ensureTarget(report, targetId, diagnostics = {}) {
  let target = report.targets.find((candidate) => candidate.target_id === targetId);
  if (!target) {
    target = {
      target_id: targetId,
      status: "unknown",
      diagnostics: clone(diagnostics) ?? {},
      artifact_paths: [],
      checks: [],
    };
    report.targets.push(target);
  } else if (diagnostics && Object.keys(diagnostics).length > 0) {
    target.diagnostics = { ...target.diagnostics, ...clone(diagnostics) };
  }
  return target;
}

export function recordCheck(report, targetId, check) {
  const target = ensureTarget(report, targetId);
  const normalized = {
    check_id: String(check.check_id ?? check.id ?? "").trim().toLowerCase(),
    status: normalizeStatus(check.status),
    runner: String(check.runner ?? report.runner),
    diagnostics: clone(check.diagnostics) ?? {},
    artifact_paths: Array.isArray(check.artifact_paths) ? [...check.artifact_paths] : [],
  };
  if (!normalized.check_id) {
    throw new Error(`Semantic check for target ${targetId} is missing check_id`);
  }
  const existingIndex = target.checks.findIndex((candidate) => candidate.check_id === normalized.check_id);
  if (existingIndex >= 0) {
    target.checks[existingIndex] = normalized;
  } else {
    target.checks.push(normalized);
  }
  for (const artifactPath of normalized.artifact_paths) {
    if (!target.artifact_paths.includes(artifactPath)) {
      target.artifact_paths.push(artifactPath);
    }
  }
  return normalized;
}

export function mergeDiagnostics(...diagnosticSets) {
  const merged = {};
  for (const diagnostics of diagnosticSets) {
    if (!diagnostics || typeof diagnostics !== "object") {
      continue;
    }
    Object.assign(merged, clone(diagnostics));
  }
  return merged;
}

export function finalizeReport(report, spec) {
  const requiredChecks = new Set(spec.required_checks ?? []);
  const summary = report.summary ?? {};
  const counts = {
    passed: 0,
    failed: 0,
    warning: 0,
    skipped: 0,
    not_applicable: 0,
    unknown: 0,
  };
  const failedChecks = [];
  const optionalFailedChecks = [];
  for (const target of report.targets) {
    let targetStatus = "passed";
    for (const check of target.checks) {
      counts[check.status] = (counts[check.status] ?? 0) + 1;
      const compositeId = target.target_id === "_global" ? check.check_id : `${target.target_id}/${check.check_id}`;
      if (requiredChecks.has(check.check_id)) {
        if (check.status !== "passed") {
          targetStatus = "failed";
          failedChecks.push(compositeId);
        }
      } else if (check.status !== "passed" && check.status !== "not_applicable") {
        optionalFailedChecks.push(compositeId);
        if (targetStatus !== "failed") {
          targetStatus = "warning";
        }
      }
    }
    if (target.checks.length === 0) {
      targetStatus = "unknown";
    }
    target.status = targetStatus;
  }
  summary.check_counts = counts;
  summary.failed_checks = failedChecks;
  summary.optional_failed_checks = optionalFailedChecks;
  if (failedChecks.length > 0) {
    summary.status = "failed";
  } else if (optionalFailedChecks.length > 0) {
    summary.status = "warning";
  } else if (report.targets.length === 0) {
    summary.status = "unknown";
  } else {
    summary.status = "passed";
  }
  report.summary = summary;
  return report;
}

export async function writeReport(spec, report) {
  const reportPath = String(spec.report_path ?? report.report_path);
  await fs.mkdir(path.dirname(reportPath), { recursive: true });
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

export function buildArtifactPath(spec, targetId, label, extension = "json") {
  const reportPath = String(spec.report_path ?? "semantic-report.json");
  const artifactDir = path.dirname(reportPath);
  const safeTarget = String(targetId).replace(/[^a-zA-Z0-9._-]+/g, "-");
  const safeLabel = String(label).replace(/[^a-zA-Z0-9._-]+/g, "-");
  return path.join(artifactDir, `${safeTarget}-${safeLabel}.${extension}`);
}

export function toPlainRect(rect) {
  if (!rect) {
    return null;
  }
  const left = Number(rect.left ?? rect.x ?? 0);
  const top = Number(rect.top ?? rect.y ?? 0);
  const width = Number(rect.width ?? Math.max(0, Number(rect.right ?? left) - left));
  const height = Number(rect.height ?? Math.max(0, Number(rect.bottom ?? top) - top));
  return {
    left,
    top,
    width,
    height,
    right: Number(rect.right ?? left + width),
    bottom: Number(rect.bottom ?? top + height),
  };
}

export function rectIntersects(a, b) {
  if (!a || !b) {
    return false;
  }
  return !(a.right <= b.left || a.left >= b.right || a.bottom <= b.top || a.top >= b.bottom);
}

export function rectContainsPoint(rect, point) {
  if (!rect || !point) {
    return false;
  }
  return point.x >= rect.left && point.x <= rect.right && point.y >= rect.top && point.y <= rect.bottom;
}

export function compareExpectedMap(actual, expected) {
  const mismatches = [];
  for (const [key, expectedValue] of Object.entries(expected ?? {})) {
    const actualValue = actual?.[key];
    if (Array.isArray(expectedValue)) {
      const normalizedActual = Array.isArray(actualValue) ? actualValue : [actualValue];
      const missing = expectedValue.filter((value) => !normalizedActual.includes(value));
      if (missing.length > 0) {
        mismatches.push({ key, expected: expectedValue, actual: actualValue });
      }
      continue;
    }
    if (expectedValue && typeof expectedValue === "object" && !Array.isArray(expectedValue)) {
      for (const [operator, operatorValue] of Object.entries(expectedValue)) {
        const numericActual = Number(actualValue);
        const numericExpected = Number(operatorValue);
        const passed =
          (operator === "eq" && actualValue === operatorValue) ||
          (operator === "gte" && numericActual >= numericExpected) ||
          (operator === "lte" && numericActual <= numericExpected) ||
          (operator === "gt" && numericActual > numericExpected) ||
          (operator === "lt" && numericActual < numericExpected) ||
          (operator === "contains" && String(actualValue ?? "").includes(String(operatorValue)));
        if (!passed) {
          mismatches.push({ key, operator, expected: operatorValue, actual: actualValue });
        }
      }
      continue;
    }
    if (actualValue !== expectedValue) {
      mismatches.push({ key, expected: expectedValue, actual: actualValue });
    }
  }
  return mismatches;
}

export async function runHook(modulePath, exportName, ...args) {
  if (!modulePath) {
    return null;
  }
  const imported = await import(modulePath);
  const hook = exportName ? imported?.[exportName] : imported?.default;
  if (typeof hook !== "function") {
    return null;
  }
  return hook(...args);
}
