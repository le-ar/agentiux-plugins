#!/usr/bin/env node

import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { setTimeout as delay } from "node:timers/promises";

function parseArgs(argv) {
  const config = {
    url: "",
    width: 1440,
    height: 1600,
    settleMs: 1200,
    waitTimeoutMs: 15000,
    screenshotPath: "",
    label: "",
    chromePath: process.env.CHROME_BINARY || "",
    waitFor:
      "document.readyState === 'complete' && !document.querySelector('.loading-shell')",
    selector: [],
    containerSelector: [],
    textSelector: [],
    allowSelector: [],
    interactionScript: "",
    interactionSettleMs: 250,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    const next = argv[index + 1];
    switch (token) {
      case "--url":
        config.url = String(next || "");
        index += 1;
        break;
      case "--width":
        config.width = Number(next || 0) || config.width;
        index += 1;
        break;
      case "--height":
        config.height = Number(next || 0) || config.height;
        index += 1;
        break;
      case "--settle-ms":
        config.settleMs = Number(next || 0) || config.settleMs;
        index += 1;
        break;
      case "--wait-timeout-ms":
        config.waitTimeoutMs = Number(next || 0) || config.waitTimeoutMs;
        index += 1;
        break;
      case "--screenshot-path":
        config.screenshotPath = String(next || "");
        index += 1;
        break;
      case "--label":
        config.label = String(next || "");
        index += 1;
        break;
      case "--chrome-path":
        config.chromePath = String(next || "");
        index += 1;
        break;
      case "--wait-for":
        config.waitFor = String(next || config.waitFor);
        index += 1;
        break;
      case "--selector":
        config.selector.push(String(next || ""));
        index += 1;
        break;
      case "--container-selector":
        config.containerSelector.push(String(next || ""));
        index += 1;
        break;
      case "--text-selector":
        config.textSelector.push(String(next || ""));
        index += 1;
        break;
      case "--allow-selector":
        config.allowSelector.push(String(next || ""));
        index += 1;
        break;
      case "--interaction-script":
        config.interactionScript = String(next || "");
        index += 1;
        break;
      case "--interaction-settle-ms":
        config.interactionSettleMs = Number(next || 0) || config.interactionSettleMs;
        index += 1;
        break;
      default:
        break;
    }
  }
  if (!config.url) {
    throw new Error("Missing required --url");
  }
  return config;
}

async function reservePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Unable to reserve debugging port"));
        return;
      }
      const port = address.port;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(port);
      });
    });
  });
}

async function waitForJson(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) {
        return await response.json();
      }
      lastError = new Error(`Unexpected HTTP ${response.status} from ${url}`);
    } catch (error) {
      lastError = error;
    }
    await delay(120);
  }
  throw lastError || new Error(`Timed out waiting for ${url}`);
}

function defaultChromePath() {
  const candidates = [
    process.env.CHROME_BINARY,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ].filter(Boolean);
  return candidates[0] || "";
}

async function loadRuleCatalog() {
  try {
    const payload = JSON.parse(
      await fs.readFile(new URL("../catalogs/layout_audit_rules.json", import.meta.url), "utf8"),
    );
    if (!payload || !Array.isArray(payload.rules)) {
      throw new Error("Rule catalog must contain a rules array");
    }
    return payload;
  } catch (error) {
    return {
      schema_version: 1,
      catalog_id: "layout-audit-rules",
      rules: [],
      catalog_error: error instanceof Error ? error.message : String(error),
    };
  }
}

class CdpConnection {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
  }

  async open() {
    await new Promise((resolve, reject) => {
      const ws = new WebSocket(this.wsUrl);
      this.ws = ws;
      ws.addEventListener("open", () => resolve());
      ws.addEventListener("error", (event) => reject(event.error || new Error("WebSocket open failed")));
      ws.addEventListener("message", (event) => this.#onMessage(event));
      ws.addEventListener("close", () => {
        for (const entry of this.pending.values()) {
          entry.reject(new Error("CDP socket closed"));
        }
        this.pending.clear();
      });
    });
  }

  async close() {
    if (!this.ws) return;
    this.ws.close();
    await delay(50);
  }

  async send(method, params = {}, sessionId = undefined) {
    const id = this.nextId;
    this.nextId += 1;
    const payload = { id, method, params };
    if (sessionId) {
      payload.sessionId = sessionId;
    }
    return await new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }

  waitFor(method, { sessionId = undefined, timeoutMs = 10000, predicate = null } = {}) {
    return new Promise((resolve, reject) => {
      const deadline = Date.now() + timeoutMs;
      const listener = (message) => {
        if (message.method !== method) return false;
        if (sessionId && message.sessionId !== sessionId) return false;
        if (predicate && !predicate(message.params || {})) return false;
        resolve(message);
        return true;
      };
      this.listeners.push(listener);
      const timer = setInterval(() => {
        if (Date.now() < deadline) {
          return;
        }
        clearInterval(timer);
        this.listeners = this.listeners.filter((item) => item !== listener);
        reject(new Error(`Timed out waiting for CDP event ${method}`));
      }, 100);
    });
  }

  #onMessage(event) {
    const payload = JSON.parse(event.data);
    if (payload.id) {
      const pending = this.pending.get(payload.id);
      if (!pending) {
        return;
      }
      this.pending.delete(payload.id);
      if (payload.error) {
        pending.reject(new Error(payload.error.message || `CDP error for ${payload.id}`));
        return;
      }
      pending.resolve(payload.result || {});
      return;
    }
    if (!payload.method) {
      return;
    }
    const active = [];
    for (const listener of this.listeners) {
      let matched = false;
      try {
        matched = listener(payload) === true;
      } catch (error) {
        matched = true;
      }
      if (!matched) {
        active.push(listener);
      }
    }
    this.listeners = active;
  }
}

function auditExpression(config, ruleCatalog) {
  const selector = JSON.stringify(config.selector);
  const containerSelector = JSON.stringify(config.containerSelector);
  const textSelector = JSON.stringify(config.textSelector);
  const allowSelector = JSON.stringify(config.allowSelector);
  const catalogPayload = JSON.stringify(ruleCatalog || { rules: [] });
  return `(() => {
    const selectorList = ${selector};
    const containerList = ${containerSelector};
    const textList = ${textSelector};
    const allowList = ${allowSelector};
    const ruleCatalog = ${catalogPayload};
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const issues = [];
    const warnings = [];
    const MAX_TEXT_ISSUES = 40;
    const MAX_PAIR_ISSUES = 80;
    const MAX_OVERFLOW_ISSUES = 80;
    const MAX_WARNINGS = 120;

    function ruleThreshold(ruleId, key, fallback) {
      const rules = Array.isArray(ruleCatalog.rules) ? ruleCatalog.rules : [];
      const rule = rules.find((entry) => String(entry?.id || "") === ruleId);
      const value = Number(rule?.thresholds?.[key]);
      return Number.isFinite(value) ? value : fallback;
    }

    const EPSILON = ruleThreshold("viewport-overflow", "epsilon_px", 2);
    const PAIR_OVERLAP_EPSILON = ruleThreshold("pair-overlap", "epsilon_px", 2);
    const PAIR_OVERLAP_RATIO = ruleThreshold("pair-overlap", "ratio_threshold", 0.04);
    const SIBLING_GAP_TOLERANCE = ruleThreshold("sibling-gap-inconsistency", "difference_px", 8);
    const SIBLING_GAP_RATIO = ruleThreshold("sibling-gap-inconsistency", "ratio_threshold", 0.3);
    const ALIGNMENT_DRIFT_TOLERANCE = ruleThreshold("alignment-drift", "drift_px", 8);
    const VERTICAL_RHYTHM_TOLERANCE = ruleThreshold("vertical-rhythm-drift", "difference_px", 12);
    const VERTICAL_RHYTHM_RATIO = ruleThreshold("vertical-rhythm-drift", "ratio_threshold", 0.3);
    const TOUCH_TARGET_MIN_WIDTH = ruleThreshold("touch-target-too-small", "min_width", 44);
    const TOUCH_TARGET_MIN_HEIGHT = ruleThreshold("touch-target-too-small", "min_height", 44);
    const FLEX_DISTRIBUTION_RATIO = ruleThreshold("unexpected-flex-distribution", "ratio_threshold", 1.8);
    const FLEX_CROSS_AXIS_TOLERANCE = ruleThreshold("unexpected-flex-distribution", "cross_axis_tolerance_px", 16);
    const CONTAINER_PADDING_TOLERANCE = ruleThreshold("container-padding-imbalance", "difference_px", 8);
    const CONTAINER_PADDING_RATIO = ruleThreshold("container-padding-imbalance", "ratio_threshold", 0.25);
    const CONTRAST_MIN_RATIO = ruleThreshold("contrast-warning", "minimum_ratio", 4.5);
    const RAGGED_GRID_EDGE_DRIFT = ruleThreshold("ragged-grid-warning", "edge_drift_px", 12);
    const CLUSTER_BAND_TOLERANCE = 24;
    const seenWarnings = new Set();

    function matchesAny(element, selectors) {
      return selectors.some((entry) => entry && element.matches(entry));
    }

    function withinAllowedOverlay(element) {
      return allowList.some((entry) => entry && element.closest(entry));
    }

    function isVisible(element) {
      if (!(element instanceof Element)) return false;
      if (withinAllowedOverlay(element)) return false;
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") === 0) {
        return false;
      }
      const rect = element.getBoundingClientRect();
      return rect.width > 1 && rect.height > 1;
    }

    function toRect(element) {
      const rect = element.getBoundingClientRect();
      return {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      };
    }

    function label(element) {
      const testId = element.getAttribute("data-testid");
      const screenId = element.getAttribute("data-screen-id");
      const id = element.getAttribute("id");
      const classes = Array.from(element.classList || []).slice(0, 3).join(".");
      const text = (element.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 48);
      return testId || screenId || id || classes || text || element.tagName.toLowerCase();
    }

    function pushWarning(payload) {
      if (!payload || warnings.length >= MAX_WARNINGS) return;
      const key = JSON.stringify([
        payload.type,
        payload.axis || "",
        payload.container || "",
        payload.target_id || "",
        payload.label || "",
        ...(payload.labels || []),
        ...(payload.target_ids || []),
      ]);
      if (seenWarnings.has(key)) {
        return;
      }
      seenWarnings.add(key);
      warnings.push(payload);
    }

    function rectsIntersect(a, b) {
      const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return { width, height, area: width > 0 && height > 0 ? width * height : 0 };
    }

    function rectArea(rect) {
      return Math.max(0, rect.width) * Math.max(0, rect.height);
    }

    function rectCenterX(rect) {
      return rect.left + rect.width / 2;
    }

    function rectCenterY(rect) {
      return rect.top + rect.height / 2;
    }

    function unionRect(rects) {
      if (!rects.length) {
        return null;
      }
      const left = Math.min(...rects.map((rect) => rect.left));
      const top = Math.min(...rects.map((rect) => rect.top));
      const right = Math.max(...rects.map((rect) => rect.right));
      const bottom = Math.max(...rects.map((rect) => rect.bottom));
      return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top,
      };
    }

    function toAuditItem(element) {
      return {
        element,
        label: label(element),
        rect: toRect(element),
      };
    }

    function clusterByAxis(items, axis) {
      const keyed = items
        .map((item) => ({
          ...item,
          clusterCenter: axis === "row" ? rectCenterY(item.rect) : rectCenterX(item.rect),
        }))
        .sort((left, right) => left.clusterCenter - right.clusterCenter);
      const clusters = [];
      let current = [];
      let currentCenter = 0;
      for (const item of keyed) {
        if (!current.length) {
          current = [item];
          currentCenter = item.clusterCenter;
          continue;
        }
        if (Math.abs(item.clusterCenter - currentCenter) <= CLUSTER_BAND_TOLERANCE) {
          current.push(item);
          currentCenter = current.reduce((sum, entry) => sum + entry.clusterCenter, 0) / current.length;
          continue;
        }
        clusters.push(current);
        current = [item];
        currentCenter = item.clusterCenter;
      }
      if (current.length) {
        clusters.push(current);
      }
      for (const cluster of clusters) {
        cluster.sort((left, right) =>
          axis === "row" ? left.rect.left - right.rect.left : left.rect.top - right.rect.top,
        );
      }
      return clusters;
    }

    function neighborGaps(cluster, axis) {
      const gaps = [];
      for (let index = 0; index < cluster.length - 1; index += 1) {
        const leftItem = cluster[index];
        const rightItem = cluster[index + 1];
        const gapValue =
          axis === "row"
            ? rightItem.rect.left - leftItem.rect.right
            : rightItem.rect.top - leftItem.rect.bottom;
        gaps.push({
          first: leftItem.label,
          second: rightItem.label,
          gap: gapValue,
        });
      }
      return gaps;
    }

    function gapWarningFromCluster(cluster, axis) {
      const gaps = neighborGaps(cluster, axis).filter((entry) => entry.gap >= 0);
      if (gaps.length < 2) {
        return null;
      }
      const values = gaps.map((entry) => entry.gap);
      const maxGap = Math.max(...values);
      const minGap = Math.min(...values);
      const difference = maxGap - minGap;
      const dominantGap = maxGap > 0 ? maxGap : 0;
      const differenceRatio = dominantGap > 0 ? difference / dominantGap : 0;
      if (difference <= SIBLING_GAP_TOLERANCE || differenceRatio < SIBLING_GAP_RATIO) {
        return null;
      }
      return {
        type: "sibling-gap-inconsistency",
        severity: "warning",
        axis: axis === "row" ? "horizontal" : "vertical",
        labels: cluster.map((item) => item.label),
        gaps,
        difference,
        difference_ratio: differenceRatio,
      };
    }

    function alignmentWarningFromCluster(cluster, axis) {
      if (cluster.length < 2) {
        return null;
      }
      const rects = cluster.map((item) => item.rect);
      const candidates =
        axis === "row"
          ? {
              top: Math.max(...rects.map((rect) => rect.top)) - Math.min(...rects.map((rect) => rect.top)),
              bottom: Math.max(...rects.map((rect) => rect.bottom)) - Math.min(...rects.map((rect) => rect.bottom)),
              center: Math.max(...rects.map((rect) => rectCenterY(rect))) - Math.min(...rects.map((rect) => rectCenterY(rect))),
            }
          : {
              left: Math.max(...rects.map((rect) => rect.left)) - Math.min(...rects.map((rect) => rect.left)),
              right: Math.max(...rects.map((rect) => rect.right)) - Math.min(...rects.map((rect) => rect.right)),
              center: Math.max(...rects.map((rect) => rectCenterX(rect))) - Math.min(...rects.map((rect) => rectCenterX(rect))),
            };
      const [anchor, drift] = Object.entries(candidates).sort((left, right) => left[1] - right[1])[0];
      if (drift <= ALIGNMENT_DRIFT_TOLERANCE) {
        return null;
      }
      return {
        type: "alignment-drift",
        severity: "warning",
        axis: axis === "row" ? "horizontal" : "vertical",
        anchor,
        labels: cluster.map((item) => item.label),
        drift,
      };
    }

    function flexDistributionWarningFromCluster(cluster, axis) {
      if (cluster.length < 2) {
        return null;
      }
      const primarySizes = cluster.map((item) => (axis === "row" ? item.rect.width : item.rect.height));
      const secondarySizes = cluster.map((item) => (axis === "row" ? item.rect.height : item.rect.width));
      const minPrimary = Math.min(...primarySizes);
      const maxPrimary = Math.max(...primarySizes);
      if (minPrimary <= 0 || maxPrimary / minPrimary < FLEX_DISTRIBUTION_RATIO) {
        return null;
      }
      if (Math.max(...secondarySizes) - Math.min(...secondarySizes) > FLEX_CROSS_AXIS_TOLERANCE) {
        return null;
      }
      return {
        type: "unexpected-flex-distribution",
        severity: "warning",
        axis: axis === "row" ? "horizontal" : "vertical",
        labels: cluster.map((item) => item.label),
        sizes: primarySizes,
        ratio: maxPrimary / minPrimary,
      };
    }

    function verticalRhythmWarningFromCluster(cluster) {
      const ordered = [...cluster].sort((left, right) => left.rect.top - right.rect.top);
      const gaps = neighborGaps(ordered, "column").filter((entry) => entry.gap >= 0);
      if (gaps.length < 2) {
        return null;
      }
      const values = gaps.map((entry) => entry.gap);
      const maxGap = Math.max(...values);
      const minGap = Math.min(...values);
      const difference = maxGap - minGap;
      const dominantGap = maxGap > 0 ? maxGap : 0;
      const differenceRatio = dominantGap > 0 ? difference / dominantGap : 0;
      if (difference <= VERTICAL_RHYTHM_TOLERANCE || differenceRatio < VERTICAL_RHYTHM_RATIO) {
        return null;
      }
      return {
        type: "vertical-rhythm-drift",
        severity: "warning",
        labels: ordered.map((item) => item.label),
        gaps,
        difference,
        difference_ratio: differenceRatio,
      };
    }

    function isFlowChild(element) {
      if (!isVisible(element)) return false;
      const style = window.getComputedStyle(element);
      return style.position !== "absolute" && style.position !== "fixed";
    }

    function flowChildren(container) {
      return Array.from(container.children || []).filter(isFlowChild).map(toAuditItem);
    }

    function containerPaddingWarning(container, items) {
      if (items.length < 2) {
        return null;
      }
      const containerRect = toRect(container);
      const contentRect = unionRect(items.map((item) => item.rect));
      if (!contentRect || containerRect.width <= 0 || (contentRect.width / containerRect.width) < 0.45) {
        return null;
      }
      const leftGutter = Math.max(0, contentRect.left - containerRect.left);
      const rightGutter = Math.max(0, containerRect.right - contentRect.right);
      const difference = Math.abs(leftGutter - rightGutter);
      const dominantGutter = Math.max(leftGutter, rightGutter);
      const differenceRatio = dominantGutter > 0 ? difference / dominantGutter : 0;
      if (difference <= CONTAINER_PADDING_TOLERANCE || differenceRatio < CONTAINER_PADDING_RATIO) {
        return null;
      }
      return {
        type: "container-padding-imbalance",
        severity: "warning",
        axis: "horizontal",
        container: label(container),
        left_gutter: leftGutter,
        right_gutter: rightGutter,
        difference,
        difference_ratio: differenceRatio,
      };
    }

    function raggedGridWarning(container, items) {
      if (items.length < 4) {
        return null;
      }
      const style = window.getComputedStyle(container);
      const isGrid = style.display.includes("grid");
      const isWrappedFlex = style.display.includes("flex") && style.flexWrap && style.flexWrap !== "nowrap";
      if (!isGrid && !isWrappedFlex) {
        return null;
      }
      const rows = clusterByAxis(items, "row");
      if (rows.length < 2) {
        return null;
      }
      const rightEdges = rows.map((row) => Math.max(...row.map((item) => item.rect.right)));
      const edgeDrift = Math.max(...rightEdges) - Math.min(...rightEdges);
      if (edgeDrift <= RAGGED_GRID_EDGE_DRIFT) {
        return null;
      }
      return {
        type: "ragged-grid-warning",
        severity: "warning",
        container: label(container),
        row_count: rows.length,
        edge_drift: edgeDrift,
        row_edges: rightEdges,
      };
    }

    function parseColor(value) {
      if (!value) return null;
      const normalized = String(value).trim().toLowerCase();
      if (!normalized || normalized === "transparent") {
        return null;
      }
      const rgbMatch = normalized.match(/^rgba?\\(([^)]+)\\)$/);
      if (rgbMatch) {
        const parts = rgbMatch[1].split(",").map((entry) => Number(entry.trim()));
        if (parts.length >= 3 && parts.slice(0, 3).every((entry) => Number.isFinite(entry))) {
          return {
            r: parts[0],
            g: parts[1],
            b: parts[2],
            a: Number.isFinite(parts[3]) ? parts[3] : 1,
          };
        }
      }
      const hexMatch = normalized.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
      if (hexMatch) {
        const raw = hexMatch[1];
        const expanded = raw.length === 3 ? raw.split("").map((entry) => entry + entry).join("") : raw;
        return {
          r: Number.parseInt(expanded.slice(0, 2), 16),
          g: Number.parseInt(expanded.slice(2, 4), 16),
          b: Number.parseInt(expanded.slice(4, 6), 16),
          a: 1,
        };
      }
      return null;
    }

    function compositeColor(top, bottom) {
      const alpha = Math.max(0, Math.min(1, Number.isFinite(top?.a) ? top.a : 1));
      return {
        r: top.r * alpha + bottom.r * (1 - alpha),
        g: top.g * alpha + bottom.g * (1 - alpha),
        b: top.b * alpha + bottom.b * (1 - alpha),
        a: 1,
      };
    }

    function effectiveBackgroundColor(element) {
      let background = { r: 255, g: 255, b: 255, a: 1 };
      let current = element;
      while (current instanceof Element) {
        const parsed = parseColor(window.getComputedStyle(current).backgroundColor);
        if (parsed) {
          background = compositeColor(parsed, background);
          if ((parsed.a ?? 1) >= 0.99) {
            break;
          }
        }
        current = current.parentElement;
      }
      return background;
    }

    function relativeLuminance(color) {
      const normalize = (channel) => {
        const value = channel / 255;
        return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * normalize(color.r) + 0.7152 * normalize(color.g) + 0.0722 * normalize(color.b);
    }

    function contrastRatio(foreground, background) {
      const foregroundLuminance = relativeLuminance(foreground);
      const backgroundLuminance = relativeLuminance(background);
      const lighter = Math.max(foregroundLuminance, backgroundLuminance);
      const darker = Math.min(foregroundLuminance, backgroundLuminance);
      return (lighter + 0.05) / (darker + 0.05);
    }

    function isInteractiveElement(element) {
      if (!(element instanceof Element)) return false;
      if (
        element.matches(
          "button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='switch'], [role='checkbox'], [role='tab'], [role='menuitem']",
        )
      ) {
        return true;
      }
      if (element.matches("a[href]")) {
        const style = window.getComputedStyle(element);
        return style.display !== "inline";
      }
      return false;
    }

    function overlapsViewport(rect) {
      return rect.right > viewport.width + EPSILON || rect.left < 0 - EPSILON;
    }

    function insideIntentionalHorizontalScrollArea(element) {
      let current = element.parentElement;
      while (current instanceof Element) {
        if (!isVisible(current)) {
          current = current.parentElement;
          continue;
        }
        const style = window.getComputedStyle(current);
        const hasHorizontalScroll =
          ["auto", "scroll"].includes(style.overflowX) ||
          current.scrollWidth > current.clientWidth + EPSILON;
        if (hasHorizontalScroll) {
          const rect = toRect(current);
          const containerFitsViewport = rect.left >= 0 - EPSILON && rect.right <= viewport.width + EPSILON;
          if (containerFitsViewport) {
            return true;
          }
        }
        current = current.parentElement;
      }
      return false;
    }

    function intersectsViewport(rect) {
      return rect.bottom > 0 && rect.top < viewport.height && rect.right > 0 && rect.left < viewport.width;
    }

    const auditElements = Array.from(
      new Set(
        selectorList.flatMap((entry) => (entry ? Array.from(document.querySelectorAll(entry)) : [])),
      ),
    ).filter(isVisible);

    const containerElements = Array.from(
      new Set(
        containerList.flatMap((entry) => (entry ? Array.from(document.querySelectorAll(entry)) : [])),
      ),
    ).filter(isVisible);

    const textElements = Array.from(
      new Set(
        textList.flatMap((entry) => (entry ? Array.from(document.querySelectorAll(entry)) : [])),
      ),
    ).filter(isVisible);

    const interactiveElements = Array.from(
      new Set(
        Array.from(
          document.querySelectorAll(
            "button, input:not([type='hidden']), select, textarea, summary, [role='button'], [role='switch'], [role='checkbox'], [role='tab'], [role='menuitem'], a[href]",
          ),
        ),
      ),
    ).filter((element) => isVisible(element) && isInteractiveElement(element));

    for (const element of auditElements) {
      const rect = toRect(element);
      if (
        overlapsViewport(rect) &&
        !insideIntentionalHorizontalScrollArea(element) &&
        issues.filter((item) => item.type === "viewport-overflow").length < MAX_OVERFLOW_ISSUES
      ) {
        issues.push({
          type: "viewport-overflow",
          label: label(element),
          rect,
        });
      }
      if (!intersectsViewport(rect)) {
        continue;
      }
      const center = {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
      };
      if (center.x < 0 || center.x >= viewport.width || center.y < 0 || center.y >= viewport.height) {
        continue;
      }
      const occupant = document.elementFromPoint(center.x, center.y);
      if (
        occupant &&
        occupant !== element &&
        !element.contains(occupant) &&
        !occupant.contains(element) &&
        !withinAllowedOverlay(occupant)
      ) {
        issues.push({
          type: "occlusion",
          label: label(element),
          occupant: label(occupant),
          point: center,
        });
      }
    }

    for (const container of containerElements) {
      const children = Array.from(container.children).filter((child) => {
        if (!isVisible(child)) return false;
        const style = window.getComputedStyle(child);
        return style.position !== "absolute" && style.position !== "fixed";
      });
      for (let leftIndex = 0; leftIndex < children.length; leftIndex += 1) {
        for (let rightIndex = leftIndex + 1; rightIndex < children.length; rightIndex += 1) {
          if (issues.filter((item) => item.type === "pair-overlap").length >= MAX_PAIR_ISSUES) {
            break;
          }
          const left = children[leftIndex];
          const right = children[rightIndex];
          if (left.contains(right) || right.contains(left)) {
            continue;
          }
          const intersection = rectsIntersect(toRect(left), toRect(right));
          if (intersection.area <= PAIR_OVERLAP_EPSILON * PAIR_OVERLAP_EPSILON) {
            continue;
          }
          const smallerArea = Math.min(rectArea(toRect(left)), rectArea(toRect(right)));
          const overlapRatio = smallerArea > 0 ? intersection.area / smallerArea : 0;
          if (overlapRatio < PAIR_OVERLAP_RATIO) {
            continue;
          }
          issues.push({
            type: "pair-overlap",
            container: label(container),
            first: label(left),
            second: label(right),
            intersection,
            overlap_ratio: overlapRatio,
          });
        }
      }
    }

    for (const element of textElements) {
      if (issues.filter((item) => item.type === "text-clipping").length >= MAX_TEXT_ISSUES) {
        break;
      }
      const style = window.getComputedStyle(element);
      if (style.display === "inline") {
        continue;
      }
      const text = (element.textContent || "").replace(/\\s+/g, " ").trim();
      if (!text) {
        continue;
      }
      const horizontallyClipped = element.scrollWidth > element.clientWidth + EPSILON;
      const verticallyClipped = element.scrollHeight > element.clientHeight + EPSILON;
      const usesClip =
        ["hidden", "clip", "auto", "scroll"].includes(style.overflowX) ||
        ["hidden", "clip", "auto", "scroll"].includes(style.overflowY) ||
        style.textOverflow === "ellipsis" ||
        style.whiteSpace === "nowrap";
      if ((horizontallyClipped || verticallyClipped) && usesClip) {
        issues.push({
          type: "text-clipping",
          label: label(element),
          text: text.slice(0, 120),
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
          scrollHeight: element.scrollHeight,
          clientHeight: element.clientHeight,
        });
      }
    }

    for (const container of containerElements) {
      const items = flowChildren(container);
      if (items.length < 2) {
        continue;
      }
      const rowClusters = clusterByAxis(items, "row");
      const columnClusters = clusterByAxis(items, "column");
      for (const cluster of rowClusters) {
        pushWarning(gapWarningFromCluster(cluster, "row"));
        pushWarning(alignmentWarningFromCluster(cluster, "row"));
        pushWarning(flexDistributionWarningFromCluster(cluster, "row"));
      }
      for (const cluster of columnClusters) {
        pushWarning(gapWarningFromCluster(cluster, "column"));
        pushWarning(alignmentWarningFromCluster(cluster, "column"));
        pushWarning(flexDistributionWarningFromCluster(cluster, "column"));
        pushWarning(verticalRhythmWarningFromCluster(cluster));
      }
      pushWarning(containerPaddingWarning(container, items));
      pushWarning(raggedGridWarning(container, items));
    }

    for (const element of interactiveElements) {
      const rect = toRect(element);
      if (rect.width >= TOUCH_TARGET_MIN_WIDTH && rect.height >= TOUCH_TARGET_MIN_HEIGHT) {
        continue;
      }
      pushWarning({
        type: "touch-target-too-small",
        severity: "warning",
        label: label(element),
        rect,
        min_width: TOUCH_TARGET_MIN_WIDTH,
        min_height: TOUCH_TARGET_MIN_HEIGHT,
      });
    }

    for (const element of textElements) {
      const text = (element.textContent || "").replace(/\\s+/g, " ").trim();
      if (!text) {
        continue;
      }
      const foreground = parseColor(window.getComputedStyle(element).color);
      const background = effectiveBackgroundColor(element);
      if (!foreground || !background) {
        continue;
      }
      const ratio = contrastRatio(compositeColor(foreground, background), background);
      if (ratio >= CONTRAST_MIN_RATIO) {
        continue;
      }
      pushWarning({
        type: "contrast-warning",
        severity: "warning",
        label: label(element),
        contrast_ratio: ratio,
        minimum_ratio: CONTRAST_MIN_RATIO,
        text: text.slice(0, 120),
      });
    }

    const responsive = [];
    for (const grid of document.querySelectorAll(".content-grid, .metric-grid")) {
      if (!isVisible(grid)) {
        continue;
      }
      responsive.push({
        label: label(grid),
        columns: window.getComputedStyle(grid).gridTemplateColumns,
        width: Math.round(grid.getBoundingClientRect().width),
      });
    }

    const navigationEntry = performance.getEntriesByType("navigation")[0] || null;
    const paintEntries = Object.fromEntries(
      performance
        .getEntriesByType("paint")
        .map((entry) => [entry.name, Math.round(entry.startTime)]),
    );
    let dashboardDebug = null;
    try {
      dashboardDebug = window.__agentiux?.debugSnapshot?.() || null;
    } catch (error) {
      dashboardDebug = null;
    }
    const activeScreenIds = Array.from(document.querySelectorAll("[data-screen-id]"))
      .filter(isVisible)
      .map((element) => element.getAttribute("data-screen-id"))
      .filter(Boolean);
    const activePanelIds = Array.from(document.querySelectorAll("[data-panel]"))
      .filter(isVisible)
      .map((element) => element.getAttribute("data-panel"))
      .filter(Boolean);
    const selectedWorkspace =
      document.querySelector("[data-selected-workspace]")?.getAttribute("data-selected-workspace") || null;
    const selectedPanel = document.querySelector("[data-selected-panel]")?.getAttribute("data-selected-panel") || null;
    const status = issues.length ? "failed" : warnings.length ? "warning" : "passed";
    return {
      viewport,
      status,
      issue_count: issues.length,
      issues,
      warning_count: warnings.length,
      warnings,
      responsive,
      title: document.title,
      location: {
        href: window.location.href,
        pathname: window.location.pathname,
        search: window.location.search,
        hash: window.location.hash,
      },
      active_screen_ids: activeScreenIds,
      active_panel_ids: activePanelIds,
      selected_workspace_path: selectedWorkspace,
      selected_panel: selectedPanel,
      dashboard_debug: dashboardDebug,
      timings: {
        response_end_ms: navigationEntry ? Math.round(navigationEntry.responseEnd) : null,
        dom_content_loaded_ms: navigationEntry ? Math.round(navigationEntry.domContentLoadedEventEnd) : null,
        load_event_ms: navigationEntry ? Math.round(navigationEntry.loadEventEnd) : null,
        first_paint_ms: paintEntries["first-paint"] ?? null,
        first_contentful_paint_ms: paintEntries["first-contentful-paint"] ?? null,
        first_usable_render_ms: dashboardDebug?.timings?.firstUsableRenderMs ?? null,
        audit_ready_ms: Math.round(performance.now()),
      },
      rule_catalog_id: ruleCatalog.catalog_id || null,
      rule_catalog_version: ruleCatalog.schema_version || null,
    };
  })()`;
}

async function waitForExpression(cdp, sessionId, expression, timeoutMs) {
  const wrapped = `(async () => {
    const deadline = Date.now() + ${timeoutMs};
    while (Date.now() < deadline) {
      try {
        if (${expression}) {
          await document.fonts?.ready?.catch?.(() => {});
          return true;
        }
      } catch (error) {
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return false;
  })()`;
  const result = await cdp.send(
    "Runtime.evaluate",
    {
      expression: wrapped,
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  return Boolean(result.result?.value);
}

async function captureScreenshot(cdp, sessionId, screenshotPath) {
  if (!screenshotPath) {
    return "";
  }
  const payload = await cdp.send(
    "Page.captureScreenshot",
    {
      format: "png",
      captureBeyondViewport: true,
      fromSurface: true,
    },
    sessionId,
  );
  await fs.mkdir(path.dirname(screenshotPath), { recursive: true });
  await fs.writeFile(screenshotPath, Buffer.from(payload.data, "base64"));
  return screenshotPath;
}

async function evaluateRuntimeExpression(cdp, sessionId, expression) {
  const result = await cdp.send(
    "Runtime.evaluate",
    {
      expression,
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId,
  );
  return result.result?.value ?? null;
}

async function runAudit(config) {
  const chromePath = config.chromePath || defaultChromePath();
  if (!chromePath) {
    throw new Error("Unable to locate Chrome or Chromium binary for browser layout audit");
  }
  const ruleCatalog = await loadRuleCatalog();
  const debugPort = await reservePort();
  const tempRoot = await fs.mkdtemp(path.join(os.tmpdir(), "agentiux-browser-audit-"));
  const chrome = spawn(
    chromePath,
    [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${debugPort}`,
      `--user-data-dir=${path.join(tempRoot, "profile")}`,
      "about:blank",
    ],
    {
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  let stderr = "";
  chrome.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  try {
    const version = await waitForJson(`http://127.0.0.1:${debugPort}/json/version`, 10000);
    const cdp = new CdpConnection(version.webSocketDebuggerUrl);
    await cdp.open();
    try {
      const created = await cdp.send("Target.createTarget", { url: "about:blank" });
      const targetId = created.targetId;
      const attached = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
      const sessionId = attached.sessionId;
      await cdp.send("Page.enable", {}, sessionId);
      await cdp.send("Runtime.enable", {}, sessionId);
      await cdp.send(
        "Emulation.setDeviceMetricsOverride",
        {
          width: config.width,
          height: config.height,
          deviceScaleFactor: 1,
          mobile: false,
        },
        sessionId,
      );
      const loadEvent = cdp.waitFor("Page.loadEventFired", {
        sessionId,
        timeoutMs: config.waitTimeoutMs,
      });
      await cdp.send("Page.navigate", { url: config.url }, sessionId);
      await loadEvent;
      const ready = await waitForExpression(cdp, sessionId, config.waitFor, config.waitTimeoutMs);
      if (!ready) {
        throw new Error(`Timed out waiting for page readiness: ${config.waitFor}`);
      }
      if (config.settleMs > 0) {
        await delay(config.settleMs);
      }
      let interactionResult = null;
      if (config.interactionScript) {
        interactionResult = await evaluateRuntimeExpression(cdp, sessionId, config.interactionScript);
        if (config.interactionSettleMs > 0) {
          await delay(config.interactionSettleMs);
        }
      }
      const evaluationValue = await evaluateRuntimeExpression(cdp, sessionId, auditExpression(config, ruleCatalog));
      const screenshotPath = await captureScreenshot(cdp, sessionId, config.screenshotPath);
      const payload = {
        ok: String(evaluationValue.status || "passed") === "passed",
        label: config.label || config.url,
        url: config.url,
        viewport: { width: config.width, height: config.height },
        screenshot_path: screenshotPath,
        chrome_path: chromePath,
        stderr_tail: stderr.split(/\n/).filter(Boolean).slice(-12),
        interaction_result: interactionResult,
        ...(evaluationValue || {}),
      };
      await cdp.send("Target.closeTarget", { targetId });
      await cdp.close();
      return payload;
    } finally {
      await cdp.close().catch(() => {});
    }
  } finally {
    chrome.kill("SIGKILL");
    await fs.rm(tempRoot, { recursive: true, force: true }).catch(() => {});
  }
}

async function main() {
  const config = parseArgs(process.argv.slice(2));
  config.selector = config.selector.length
    ? config.selector
    : [
        "[data-testid]",
        "[data-screen-id]",
        ".shell",
        ".sidebar",
        ".main",
        ".hero-card",
        ".surface-card",
        ".workspace-nav-card",
        ".portfolio-card",
        ".metric-card",
        ".attention-card",
        ".tab-button",
        ".pill-chip",
      ];
  config.containerSelector = config.containerSelector.length
    ? config.containerSelector
    : [
        "body",
        ".shell",
        ".sidebar",
        ".main",
        ".page-shell",
        ".content-grid",
        ".metric-grid",
        ".attention-strip",
        ".workspace-nav",
        ".portfolio-grid",
        ".panel-tabs",
      ];
  config.textSelector = config.textSelector.length
    ? config.textSelector
    : [
        "h1",
        "h2",
        "h3",
        "h4",
        "p",
        "button",
        "a",
        ".pill-chip",
        ".tab-button",
        ".metric-label",
        ".metric-card strong",
      ];
  config.allowSelector = config.allowSelector.length
    ? config.allowSelector
    : [".issue-popover", ".loading-shell"];
  const payload = await runAudit(config);
  process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
