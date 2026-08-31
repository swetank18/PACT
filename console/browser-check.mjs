/**
 * The console, in a real browser.
 *
 * Nobody had looked at it. The 32 unit tests include a full jsdom mount, which
 * proves the components do not throw — it does not prove a stylesheet loaded, a
 * font resolved, an asset path is right, or that a fetch the app makes at boot
 * actually returns anything. The first time this ran it found that the
 * signature parity vector 404s in the deployed build, because the file is
 * served by a Vite dev-server middleware that does not exist in a built bundle.
 *
 * Needs a running instance:
 *
 *     node browser-check.mjs http://localhost:8080 ./shots
 *
 * Exits non-zero on any console error, any failed request, or any surface that
 * renders nothing.
 */
import { mkdirSync } from "node:fs";

import { chromium } from "@playwright/test";

const base = (process.argv[2] ?? "http://127.0.0.1:8080").replace(/\/$/, "");
const out = process.argv[3] ?? "shots";
mkdirSync(out, { recursive: true });

const SURFACES = ["Merchant console", "Grant", "Checkout", "Pitch"];

/** Text every surface must have on it, so "rendered" means more than "did not
 *  crash". A blank page with a working stylesheet is still a blank page. */
const MUST_CONTAIN = {
  "Merchant console": ["GMV TODAY", "LIVE ORDERS", "GATE DECISIONS", "AUDIT TRAIL"],
  Grant: ["authority"],
  Checkout: ["mandate"],
  Pitch: [],
};

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 2,
});

const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`http ${r.status()}: ${r.url()}`); });

await page.goto(base, { waitUntil: "networkidle" });

// Beat 5 so the console has a rollback and a recovery on it — the states that
// carry a reason code, and the ones an empty screenshot would hide.
await page.keyboard.press("5");
await page.waitForTimeout(9000);

// The parity badge is the one claim on screen a judge can check by looking, and
// it is the thing that was quietly broken in the deployed build.
const header = await page.innerText("header");
if (!/SIGNATURE PARITY/i.test(header)) {
  problems.push(`the parity badge is not showing: header reads ${JSON.stringify(header)}`);
}

for (const name of SURFACES) {
  await page.getByRole("button", { name, exact: true }).click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/${name.toLowerCase().replace(/\s+/g, "-")}.png` });

  const text = await page.innerText("body");
  if (text.length < 200) problems.push(`${name} rendered ${text.length} characters`);
  for (const needle of MUST_CONTAIN[name] ?? []) {
    if (!text.includes(needle)) problems.push(`${name} is missing ${JSON.stringify(needle)}`);
  }
  console.log(`ok    ${name} — ${text.length} chars`);
}

await browser.close();

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log("\nno console errors, no failed requests, every surface rendered");
