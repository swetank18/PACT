/**
 * The product walkthrough, driven rather than clicked.
 *
 * One Chromium context at 1920x1080 records a single continuous take while it
 * performs the demo the runbook describes: grant, shop, the headroom upsell,
 * the same offer made blind, four attacks, the rollback, and the buyer's own
 * view of a blocked decision. Every segment writes a mark — a start and end
 * offset in seconds from the first frame — into marks.json, and the compositor
 * cuts the take on those marks. Nothing downstream depends on a human
 * remembering when something happened.
 *
 * Playwright draws no cursor, so one is injected: a dot that follows the mouse,
 * moved in steps toward each target before the click, because a demo where
 * things happen with no pointer in sight reads as a slideshow.
 *
 *   node record_walkthrough.mjs http://127.0.0.1:8090 ./recording
 */
import { createRequire } from "node:module";

// Resolved against console/package.json rather than by bare specifier, because
// Node resolves a bare import from the *importing file's* directory: video/ has
// no node_modules and the repository root's is empty, so
// `node ../video/record_walkthrough.mjs` — the command README_VIDEO.md
// documents — died with ERR_MODULE_NOT_FOUND no matter which directory it was
// run from. This way the three recorder scripts run from anywhere.
const { chromium } = createRequire(new URL("../console/package.json", import.meta.url))(
  "@playwright/test",
);
import { writeFileSync, mkdirSync } from "node:fs";

const base = process.argv[2] ?? "http://127.0.0.1:8090";
const out = process.argv[3] ?? "./recording";
mkdirSync(out, { recursive: true });

const CURSOR = `
  const dot = document.createElement("div");
  dot.id = "__demo_cursor";
  dot.style.cssText = [
    "position:fixed", "z-index:2147483647", "width:22px", "height:22px",
    "margin:-11px 0 0 -11px", "border-radius:50%", "pointer-events:none",
    "background:radial-gradient(circle at 35% 35%, rgba(255,255,255,.95), rgba(255,255,255,.35) 45%, rgba(255,255,255,0) 70%)",
    "box-shadow:0 0 0 1.5px rgba(255,255,255,.55), 0 0 18px 4px rgba(95,211,155,.35)",
    "transition:transform .08s ease-out", "left:-100px", "top:-100px",
  ].join(";");
  const attach = () => document.body && document.body.appendChild(dot);
  document.readyState === "loading"
    ? document.addEventListener("DOMContentLoaded", attach) : attach();
  addEventListener("mousemove", (e) => {
    dot.style.left = e.clientX + "px";
    dot.style.top = e.clientY + "px";
  });
  addEventListener("mousedown", () => (dot.style.transform = "scale(.7)"));
  addEventListener("mouseup", () => (dot.style.transform = "scale(1)"));
`;

const browser = await chromium.launch({
  args: ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--force-device-scale-factor=1"],
});
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: out, size: { width: 1920, height: 1080 } },
  deviceScaleFactor: 1,
});
await context.addInitScript(CURSOR);
const page = await context.newPage();

const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("requestfailed", (r) => errors.push("FAILED " + r.url().slice(0, 100)));

const t0 = Date.now();
const now = () => (Date.now() - t0) / 1000;
const marks = {};
const open = (name) => (marks[name] = { start: now() });
const close = (name) => (marks[name].end = now());
const wait = (ms) => page.waitForTimeout(ms);

/** Move the pointer to an element's centre in steps, then click it. */
async function point(locator, { click = true, steps = 22 } = {}) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("no box for " + locator);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps });
  await wait(260);
  if (click) await locator.click();
}

const byName = (name) => page.getByRole("button", { name, exact: true });

await page.goto(base + "/#/console", { waitUntil: "networkidle" });
await page.mouse.move(960, 980, { steps: 4 });
await wait(1200);

// A cold board, so nothing on screen predates the take.
await point(byName("reset · 0").or(page.locator("button").filter({ hasText: /^reset/ }).first()));
await wait(1800);

// ---------------------------------------------------------------- S09 grant
await point(byName("Grant"));
await wait(1400);
open("grant");
await point(byName("Grant and sign"));
await wait(5200);                       // the signature, then the mandate chip
await point(byName("Checkout"));
await wait(1200);
// Typed rather than injected: the composer is where a buyer states intent, and
// a value that simply appears reads as a mock.
const composer = page.getByPlaceholder("What should the agent buy?");
await point(composer);
await composer.type("restock office supplies for the month", { delay: 55 });
await wait(700);
await point(byName("Send"));
await wait(6500);                       // quote card, headroom bar, verdict
close("grant");

// The offer the merchant made against remaining authority, accepted by the
// person whose authority it is. This is the claim the film is built on, so it
// is performed rather than inferred from a counter.
await wait(900);
open("addon");
await point(byName("Add"));
await wait(15500);                      // re-quote, gate, order, and the bar moving
close("addon");
await wait(1600);

await point(byName("Merchant console"));
await wait(1500);

// ------------------------------------------------------------ S10-S13 beats
const beat = async (key, name, settle) => {
  open(name);
  await page.keyboard.press(key);
  await wait(settle);
  close(name);
  await wait(700);
};

await beat("1", "beat1", 13000);
await beat("2", "beat2", 12000);
await beat("3", "beat3", 12000);
await beat("4", "beat4", 15000);
await beat("5", "beat5", 21000);

// ------------------------------------------------------------- S15 firewall
await point(byName("Firewall"));
await wait(2000);
const tab = page.locator("nav button").filter({ hasText: "Transactions" }).first();
if (await tab.count()) {
  await point(tab);
  await wait(1500);
}
open("firewall");
const blocked = page.locator("tr, [role='row'], li").filter({ hasText: /BLOCK/ }).first();
if (await blocked.count()) {
  await point(blocked);
  await wait(2200);
  const replay = page.getByRole("button", { name: /Replay this decision/i }).first();
  if (await replay.count()) {
    await point(replay);
    await wait(7000);
  } else {
    await wait(6000);
  }
} else {
  await wait(9000);
}
close("firewall");
await wait(1200);

writeFileSync(`${out}/marks.json`, JSON.stringify({ marks, errors, take: now() }, null, 2));
await context.close();                  // flushes the video file
await browser.close();

console.log(JSON.stringify(marks, null, 2));
console.log("take:", now().toFixed(1), "s | console errors:", errors.length ? errors : "none");
