// Renders the always-on-top pill (the real code from orchestra/static/app.js) frame by frame,
// for the animated clip in the README.
//
//   node docs/evidence/capture-pill.mjs            writes frames to docs/assets/.frames/<scheme>/
//   python docs/evidence/make_pill_gif.py          turns them into docs/assets/pill-<scheme>.gif
//
// Animations are stepped with the Web Animations API (pause, then set currentTime), so every frame
// is exact and the clip does not depend on how fast this machine can take screenshots.
// Needs Node 22+ and Chrome or Edge (set CHROME_PATH if it is not in a standard place). It launches
// its own headless browser with a throwaway profile and closes only that process.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const framesRoot = join(root, "docs", "assets", ".frames");
const FPS = 12;

const candidates = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const chrome = candidates.find((p) => existsSync(p));
if (!chrome) { console.error("No Chrome/Edge found. Set CHROME_PATH."); process.exit(2); }

// The story the clip tells: quiet, work starts, something needs you, several things do, a failure.
const A = (...s) => s;
const story = [
  { ms: 2400, model: { kind: "idle", count: 0, headline: "All quiet", detail: "13 agents \u00b7 11 done",
    agents: A("completed", "completed", "completed", "completed", "completed", "completed", "failed") } },
  { ms: 2400, model: { kind: "running", count: 0, headline: "4 running", detail: "13 agents \u00b7 5 done",
    agents: A("completed", "completed", "running", "running", "running", "running", "completed", "completed", "completed") } },
  { ms: 3000, model: { kind: "permission", count: 1, headline: "Waiting for your permission", detail: "checkout-v2",
    agents: A("completed", "completed", "running", "waiting", "running", "completed", "completed") } },
  { ms: 2400, model: { kind: "input", count: 3, headline: "Waiting for your input", detail: "checkout-v2 \u00b7 +2 more",
    agents: A("completed", "waiting", "waiting", "running", "waiting", "completed") } },
  { ms: 2400, model: { kind: "error", count: 1, headline: "Hit an error", detail: "api-gateway",
    agents: A("completed", "completed", "failed", "failed", "running", "completed") } },
];

const app = readFileSync(join(root, "orchestra", "static", "app.js"), "utf8");
const code = app.slice(app.indexOf("const PILL_COLORS"), app.indexOf("function pillModel")) +
  app.slice(app.indexOf("const PILL_CSS"), app.indexOf("function updatePillButton"));
const work = mkdtempSync(join(tmpdir(), "pill-"));
const page = join(work, "pill.html");
writeFileSync(page, `<!doctype html><meta charset="utf-8"><body><script>
const state = { pill: null, pillUi: null };
${code}
const st = document.createElement("style"); st.textContent = PILL_CSS; document.head.appendChild(st);
state.pill = window;
window.show = (m) => renderPill(m);
window.at = (t) => document.getAnimations().forEach((a) => { a.pause(); a.currentTime = t; });
</script>`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const port = 9300 + Math.floor(Math.random() * 500);
const proc = spawn(chrome, [`--remote-debugging-port=${port}`, `--user-data-dir=${join(work, "profile")}`, "--headless=new",
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
  const run = (expression) => send("Runtime.evaluate", { expression, awaitPromise: true });
  await send("Page.enable");

  for (const scheme of ["light", "dark"]) {
    const dir = join(framesRoot, scheme);
    rmSync(dir, { recursive: true, force: true });
    mkdirSync(dir, { recursive: true });
    await send("Emulation.setDeviceMetricsOverride", { width: 360, height: 104, deviceScaleFactor: 2, mobile: false });
    await send("Emulation.setEmulatedMedia", { features: [
      { name: "prefers-color-scheme", value: scheme }, { name: "prefers-reduced-motion", value: "no-preference" }] });
    await send("Page.navigate", { url: "about:blank" });
    await send("Page.navigate", { url: pathToFileURL(page).href });
    await sleep(800);
    let n = 0;
    for (const step of story) {
      await run(`show(${JSON.stringify(step.model)})`);
      await sleep(60);
      const frames = Math.round((step.ms / 1000) * FPS);
      for (let f = 0; f < frames; f++) {
        await run(`at(${Math.round((f * 1000) / FPS)})`);
        const shot = await send("Page.captureScreenshot", { format: "png" });
        writeFileSync(join(dir, String(++n).padStart(4, "0") + ".png"), Buffer.from(shot.result.data, "base64"));
      }
    }
    console.log(scheme + ": " + n + " frames");
  }
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
  await sleep(300);
  try { rmSync(work, { recursive: true, force: true }); } catch {}
  process.exit(0);
}
