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
// ONLY (comma-separated shot names; default: all), REPORT_URL and BIG_REPORT_URL (file:// URLs of two static reports; those shots are
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

// Scrolls an Insights card (by its title) to just under the sticky header.
const cardShot = (title) => `(async () => { await new Promise(r => setTimeout(r, 600));
  const h = document.querySelector('header').getBoundingClientRect().height;
  const card = [...document.querySelectorAll('#insights h3')].find((e) => e.textContent === ${JSON.stringify(title)}).parentElement;
  window.scrollTo(0, card.getBoundingClientRect().top + window.scrollY - h - 12);
  await new Promise(r => setTimeout(r, 300)); })()`;

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
  ["19-pulse-dark", DEMO, 1440, 560, "dark",
    `(async () => { const h = document.querySelector('header').getBoundingClientRect().height;
      window.scrollTo(0, document.getElementById('pulse').offsetTop - h - 12);
      await new Promise(r => setTimeout(r, 300)); })()`],
  ["20-pulse-light", DEMO, 1440, 560, "light",
    `(async () => { const h = document.querySelector('header').getBoundingClientRect().height;
      window.scrollTo(0, document.getElementById('pulse').offsetTop - h - 12);
      await new Promise(r => setTimeout(r, 300)); })()`],
  ["21-waits-light", DEMO + "#view=insights", 1440, 640, "light",
    `(async () => { await new Promise(r => setTimeout(r, 600));
      const h = document.querySelector('header').getBoundingClientRect().height;
      const card = [...document.querySelectorAll('#insights h3')].find((e) => e.textContent === 'Waiting on you').parentElement;
      window.scrollTo(0, card.getBoundingClientRect().top + window.scrollY - h - 12);
      await new Promise(r => setTimeout(r, 300)); })()`],
  ["22-waits-dark", DEMO + "#view=insights", 1440, 640, "dark",
    `(async () => { await new Promise(r => setTimeout(r, 600));
      const h = document.querySelector('header').getBoundingClientRect().height;
      const card = [...document.querySelectorAll('#insights h3')].find((e) => e.textContent === 'Waiting on you').parentElement;
      window.scrollTo(0, card.getBoundingClientRect().top + window.scrollY - h - 12);
      await new Promise(r => setTimeout(r, 300)); })()`],
  ["23-checks-light", DEMO + "#view=insights", 1440, 560, "light", cardShot("Did they check their work?")],
  ["24-checks-dark", DEMO + "#view=insights", 1440, 560, "dark", cardShot("Did they check their work?")],
  ["25-health-dark", DEMO, 1440, 420, "dark",
    `(async () => { const h = document.querySelector('header').getBoundingClientRect().height;
      window.scrollTo(0, document.getElementById('health').getBoundingClientRect().top + window.scrollY - h - 12);
      await new Promise(r => setTimeout(r, 300)); })()`],
  ["26-what-changed-light", DEMO + "#view=insights", 1440, 640, "light", cardShot("What changed")],
  ["27-agent-diff-dark", DEMO + "#agent=" + agentId("cart"), 1440, 900, "dark",
    `(async () => { await new Promise(r => setTimeout(r, 900));
      document.querySelectorAll('#drawer details.change').forEach((d) => { d.open = true; });
      const h = [...document.querySelectorAll('#drawer h3')].find((e) => e.textContent === 'Changes');
      h.scrollIntoView({ block: 'start' }); await new Promise(r => setTimeout(r, 300)); })()`],
  ["28-produced-light", DEMO + "#view=insights", 1440, 560, "light", cardShot("What the run produced")],
  ["29-produced-dark", DEMO + "#view=insights", 1440, 560, "dark", cardShot("What the run produced")],
  ["30-told-light", DEMO + "#view=insights", 1440, 600, "light", cardShot("What each agent was told")],
  ["31-told-dark", DEMO + "#view=insights", 1440, 600, "dark", cardShot("What each agent was told")],
  ["37-prompts-light", DEMO + "#view=insights", 1440, 590, "light", cardShot("Your prompts")],
  ["38-prompts-dark", DEMO + "#view=insights", 1440, 590, "dark", cardShot("Your prompts")],
  ["39-phone-prompts-dark", DEMO + "#view=insights", 390, 900, "dark", cardShot("Your prompts")],
  ["34-waste-light", DEMO + "#view=insights", 1440, 520, "light", cardShot("Where tokens were wasted")],
  ["35-waste-dark", DEMO + "#view=insights", 1440, 520, "dark", cardShot("Where tokens were wasted")],
  ["36-phone-waste-dark", DEMO + "#view=insights", 390, 760, "dark", cardShot("Where tokens were wasted")],
  ["32-recap-light", DEMO, 1440, 300, "light",
    `(async () => { document.getElementById('recap').click(); await new Promise(r => setTimeout(r, 300)); })()`],
  ["33-fleet-titles-dark", DEMO + "#view=fleet", 1440, 610, "dark",
    `(async () => { await new Promise(r => setTimeout(r, 900));
      const h = document.querySelector('header').getBoundingClientRect().height;
      window.scrollTo(0, document.getElementById('fleet').getBoundingClientRect().top + window.scrollY - h - 70);
      await new Promise(r => setTimeout(r, 300)); })()`],
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

  // ONLY=01-timeline-light,19-pulse-dark regenerates just those shots.
  const only = process.env.ONLY ? process.env.ONLY.split(",") : null;
  for (const [name, url, w, h, scheme, script] of shots) {
    if (only && !only.includes(name)) continue;
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
