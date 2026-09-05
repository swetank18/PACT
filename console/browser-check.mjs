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

/**
 * Press a beat key and wait for it to actually finish.
 *
 * Waited on rather than slept through. The saga step delay is 0.05s locally and
 * 0.35s in the container, so a fixed timeout that is comfortable here navigates
 * away mid-beat there — which aborts the in-flight request and reports a failure
 * that is entirely the test's own fault. The beat buttons disable themselves
 * while one is running, so their state is the signal.
 */
async function beat(n) {
  await page.keyboard.press(String(n));
  await page.waitForFunction(
    (key) => {
      const button = [...document.querySelectorAll("button")]
        .find((b) => b.textContent?.trim() === key);
      return button instanceof HTMLButtonElement && !button.disabled;
    },
    String(n),
    { timeout: 60_000 },
  );
  await page.waitForTimeout(600);
}

// 1 and 2 put orders and an accepted upsell on the board, 5 adds the rollback
// and the recovery — the states that carry a reason code. A screenshot of an
// empty console proves the page loads and nothing else.
await beat(1);
await beat(2);
await beat(5);
await page.waitForTimeout(1500);

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

/**
 * The firewall, last, because it takes the viewport — once it is open the
 * header the loop above clicks through is gone.
 *
 * Driven by clicking its own sidebar rather than by navigating to each hash: a
 * full navigation aborts the open SSE connection, which this check would then
 * report as a failed request that is entirely its own fault.
 *
 * Light mode over a dark console is exactly the kind of thing that looks fine
 * in jsdom and wrong on a projector, so every tab gets a screenshot.
 */
const FIREWALL_TABS = [
  ["Dashboard", ["Threat summary", "Live activity"]],
  ["Mandates", ["Create mandate"]],
  ["Transactions", ["All transactions", "Pending approvals"]],
  ["Analytics", ["Which check catches which attack", "sim/run.py"]],
  ["Agents", ["Add agent"]],
  ["Settings", ["Signing key fingerprint", "Auto-approve threshold"]],
];

await page.getByRole("button", { name: "Firewall", exact: true }).click();
await page.waitForTimeout(800);

for (const [label, needles] of FIREWALL_TABS) {
  await page.locator("nav button").filter({ hasText: label }).first().click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${out}/firewall-${label.toLowerCase()}.png` });

  const text = await page.innerText("body");
  if (text.length < 200) problems.push(`firewall/${label} rendered ${text.length} characters`);
  for (const needle of [label, ...needles]) {
    if (!text.includes(needle)) problems.push(`firewall/${label} is missing ${JSON.stringify(needle)}`);
  }
  // The kill switch is the one control that must be on screen everywhere.
  if (!/PAUSE ALL AGENTS|RESUME ALL/.test(text)) {
    problems.push(`firewall/${label} has no kill switch`);
  }
  console.log(`ok    firewall/${label} — ${text.length} chars`);
}

await browser.close();

if (problems.length) {
  console.error(`\n${problems.length} problem(s):`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log("\nno console errors, no failed requests, every surface rendered");
