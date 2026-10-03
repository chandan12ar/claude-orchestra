// Renders the plugin icon (assets/icon.png): the cue-light mascot from the pill, on a rounded
// dark tile. The directory listing reads it through `icon` in .claude-plugin/plugin.json.
//
//   node docs/evidence/capture-icon.mjs
//
// Needs Node 22+ and Chrome or Edge (set CHROME_PATH if it is not in a standard place).
// Launches its own headless browser with a throwaway profile and closes only that process.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..");
const SIZE = 512;

const candidates = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const chrome = candidates.find((p) => existsSync(p));
if (!chrome) { console.error("No Chrome/Edge found. Set CHROME_PATH."); process.exit(2); }

const app = readFileSync(join(root, "orchestra", "static", "app.js"), "utf8");
const code = app.slice(app.indexOf("const PILL_COLORS"), app.indexOf("function pillModel")) +
  app.slice(app.indexOf("const PILL_CSS"), app.indexOf("function updatePillButton"));
const work = mkdtempSync(join(tmpdir(), "icon-"));
const page = join(work, "icon.html");
writeFileSync(page, `<!doctype html><meta charset="utf-8">
<style>
html, body { margin:0; height:auto !important; background:transparent !important; overflow:visible !important; }
.tile { width:${SIZE}px; height:${SIZE}px; border-radius:116px; display:grid; place-items:center;
  background:radial-gradient(120% 120% at 30% 25%, #3a2f7a 0%, #1a1c2b 62%); }
.tile .pill { display:block; width:330px; height:auto; padding:0; background:none; }
.tile .pill::after { display:none; }
.tile .cue, .tile .cue svg { width:330px; height:330px; }
.tile .badge { display:none; }
</style>
<body><div class="tile" id="tile"></div><script>
const state = { pill: null, pillUi: null };
${code}
const st = document.createElement("style"); st.textContent = PILL_CSS; document.head.appendChild(st);
const pill = document.createElement("div"); pill.className = "pill"; pill.setAttribute("data-kind", "permission");
pill.style.setProperty("--c", PILL_COLORS.permission);
const cue = document.createElement("div"); cue.className = "cue"; cue.appendChild(buildPillAvatar(document));
pill.appendChild(cue); document.getElementById("tile").appendChild(pill);
document.getAnimations().forEach((a) => { a.pause(); a.currentTime = 1300; });
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
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: SIZE, height: SIZE, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "dark" }] });
  await send("Page.navigate", { url: pathToFileURL(page).href });
  await sleep(900);
  const shot = await send("Page.captureScreenshot", { format: "png", clip: { x: 0, y: 0, width: SIZE, height: SIZE, scale: 1 } });
  mkdirSync(join(root, "assets"), { recursive: true });
  writeFileSync(join(root, "assets", "icon.png"), Buffer.from(shot.result.data, "base64"));
  console.log("saved assets/icon.png");
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
  await sleep(300);
  try { rmSync(work, { recursive: true, force: true }); } catch {}
  process.exit(0);
}
