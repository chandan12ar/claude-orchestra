// Regenerates every screenshot in docs/evidence/screenshots from a running demo.
//
//   python -m orchestra --demo --no-open --port 8766 --token demo     (leave running)
//   node docs/evidence/capture.mjs
//
// Needs Node 22+ (built-in WebSocket) and Chrome or Edge. Set CHROME_PATH if it is not
// in a standard location. It launches its own headless browser with a throwaway profile
// and closes only that process, so it never touches a browser you have open.
//
// Optional: DEMO_URL (default http://127.0.0.1:8766/?k=demo&session=demo-checkout-v2),
// REPORT_URL and BIG_REPORT_URL (file:// URLs of two static reports; those shots are
// skipped when unset).
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, "screenshots");
mkdirSync(outDir, { recursive: true });

const DEMO = process.env.DEMO_URL || "http://127.0.0.1:8766/?k=demo&session=demo-checkout-v2";
const REPORT = process.env.REPORT_URL || "";
const BIG = process.env.BIG_REPORT_URL || "";

const candidates = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const chrome = candidates.find((p) => existsSync(p));
if (!chrome) { console.error("No Chrome/Edge found. Set CHROME_PATH."); process.exit(2); }

const agentId = (key) => "a" + createHash("sha1").update(key).digest("hex").slice(0, 16);
const press = (init) => `document.dispatchEvent(new KeyboardEvent('keydown', ${JSON.stringify({ bubbles: true, ...init })}))`;
const palette = (query) => `(async () => { ${press({ key: "k", ctrlKey: true })};
  const i = document.getElementById('palette-input'); i.value = ${JSON.stringify(query)};
  i.dispatchEvent(new Event('input')); await new Promise(r => setTimeout(r, 900)); })()`;

// name, url, width, height, colour scheme, optional script run before the shot
const shots = [
  ["01-timeline-light", DEMO, 1440, 900, "light"],
  ["02-timeline-dark", DEMO, 1440, 900, "dark"],
  ["03-insights-light", DEMO + "#view=insights", 1440, 1500, "light"],
  ["04-insights-dark", DEMO + "#view=insights", 1440, 1500, "dark"],
  ["05-graph-light", DEMO + "#view=graph", 1440, 960, "light"],
  ["06-graph-dark", DEMO + "#view=graph", 1440, 960, "dark"],
  ["07-workfloor-by-role-light", DEMO + "#view=workfloor", 1440, 1000, "light"],
  ["08-workfloor-by-status-dark", DEMO + "#view=workfloor", 1440, 1000, "dark",
    `(async () => { const s = document.getElementById('floor-group'); s.value = 'status';
      s.dispatchEvent(new Event('change')); await new Promise(r => setTimeout(r, 400)); })()`],
  ["09-palette-search-light", DEMO, 1440, 900, "light", palette("checkout")],
  ["10-palette-loop-dark", DEMO, 1440, 900, "dark", palette("e2e")],
  ["11-shortcuts-help-light", DEMO, 1440, 900, "light", press({ key: "?" })],
  ["12-agent-drawer-dark", DEMO + "#agent=" + agentId("unit"), 1440, 900, "dark"],
  ["13-fleet-light", DEMO + "#view=fleet", 1440, 700, "light"],
  ["14-phone-timeline-light", DEMO, 390, 844, "light"],
  ["15-phone-insights-dark", DEMO + "#view=insights", 390, 1500, "dark"],
  ...(REPORT ? [
    ["16-static-report-insights-dark", REPORT + "#view=insights", 1280, 900, "dark"],
    ["17-static-report-search-light", REPORT, 1280, 760, "light", palette("checkout")],
  ] : []),
  ...(BIG ? [["18-graph-36-agents-light", BIG + "#view=graph", 1440, 900, "light"]] : []),
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const profile = mkdtempSync(join(tmpdir(), "evidence-"));
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, [`--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, "--headless=new",
  "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "about:blank"], { stdio: "ignore" });

let ws;
try {
  let targets = [];
  for (let i = 0; i < 60 && !targets.length; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(200); }
  }
  ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0;
  const pending = new Map();
  ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  await send("Page.enable");

  for (const [name, url, w, h, scheme, script] of shots) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
    await send("Page.navigate", { url: "about:blank" });
    await send("Page.navigate", { url });
    await sleep(2600);
    if (script) {
      const r = await send("Runtime.evaluate", { expression: script, awaitPromise: true });
      if (r.result?.exceptionDetails) console.error(name, "script error:", JSON.stringify(r.result.exceptionDetails).slice(0, 200));
      await sleep(700);
    }
    const shot = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(outDir, name + ".png"), Buffer.from(shot.result.data, "base64"));
    console.log("saved", name + ".png");
  }
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
  await sleep(300);
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
  process.exit(0);
}
