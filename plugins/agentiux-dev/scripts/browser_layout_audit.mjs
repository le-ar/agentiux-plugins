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

function auditExpression(config) {
  const selector = JSON.stringify(config.selector);
  const containerSelector = JSON.stringify(config.containerSelector);
  const textSelector = JSON.stringify(config.textSelector);
  const allowSelector = JSON.stringify(config.allowSelector);
  return `(() => {
    const selectorList = ${selector};
    const containerList = ${containerSelector};
    const textList = ${textSelector};
    const allowList = ${allowSelector};
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    const issues = [];
    const EPSILON = 2;
    const MAX_TEXT_ISSUES = 40;
    const MAX_PAIR_ISSUES = 80;
    const MAX_OVERFLOW_ISSUES = 80;

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

    function rectsIntersect(a, b) {
      const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      return { width, height, area: width > 0 && height > 0 ? width * height : 0 };
    }

    function overlapsViewport(rect) {
      return rect.right > viewport.width + EPSILON || rect.left < 0 - EPSILON;
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

    for (const element of auditElements) {
      const rect = toRect(element);
      if (overlapsViewport(rect) && issues.filter((item) => item.type === "viewport-overflow").length < MAX_OVERFLOW_ISSUES) {
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
          if (intersection.area <= EPSILON * EPSILON) {
            continue;
          }
          issues.push({
            type: "pair-overlap",
            container: label(container),
            first: label(left),
            second: label(right),
            intersection,
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

    return {
      viewport,
      issue_count: issues.length,
      issues,
      responsive,
      title: document.title,
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

async function runAudit(config) {
  const chromePath = config.chromePath || defaultChromePath();
  if (!chromePath) {
    throw new Error("Unable to locate Chrome or Chromium binary for browser layout audit");
  }
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
      const evaluated = await cdp.send(
        "Runtime.evaluate",
        {
          expression: auditExpression(config),
          returnByValue: true,
          awaitPromise: true,
        },
        sessionId,
      );
      const screenshotPath = await captureScreenshot(cdp, sessionId, config.screenshotPath);
      const payload = {
        ok: (evaluated.result?.value?.issue_count || 0) === 0,
        label: config.label || config.url,
        url: config.url,
        viewport: { width: config.width, height: config.height },
        screenshot_path: screenshotPath,
        chrome_path: chromePath,
        stderr_tail: stderr.split(/\n/).filter(Boolean).slice(-12),
        ...(evaluated.result?.value || {}),
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
