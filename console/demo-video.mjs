/**
 * The whole demo, driven and recorded, so there is a backup video.
 *
 * LANE-C and LANE-B both end with "six rehearsals, backup video recorded", and
 * the video is the half that does not depend on remembering anything. A live
 * demo has four ways to die on stage — no network, a cold instance, a laptop
 * that decides to update, a projector that will not take the resolution — and
 * the answer to all four is a file that plays.
 *
 * Recorded by driving the real console against a real instance, not edited
 * together from screenshots. Everything in the frame happened:
 *
 *     node demo-video.mjs http://localhost:8080 ../docs/demo
 *
 * It fails rather than shipping a bad take. Any console error, any failed
 * request, any 4xx or 5xx, any beat that does not finish, and it exits non-zero
 * and tells you the recording is not usable — because a backup video with a
 * silently broken beat in it is worse than no backup video, and you will not be
 * watching it closely when you need it.
 *
 * It also prints how long each beat took. That is the run of show: the numbers
 * in `docs/RUNBOOK.md` came from here rather than from a stopwatch.
 */
import { mkdirSync, renameSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { chromium } from "@playwright/test";

const base = (process.argv[2] ?? "http://127.0.0.1:8080").replace(/\/$/, "");
const out = process.argv[3] ?? "video";
const captions = !process.argv.includes("--no-captions");
mkdirSync(out, { recursive: true });

// 1280x800 rather than the browser-check's 1600x1000. The video is for a
// projector and a phone, in that order, and every extra pixel is a megabyte
// nobody watching a backup video is grateful for.
const SIZE = { width: 1280, height: 800 };

const browser = await chromium.launch();
const context = await browser.newContext({
  viewport: SIZE,
  recordVideo: { dir: out, size: SIZE },
});
const page = await context.newPage();

const problems = [];

/**
 * An SSE connection that the browser aborted is not a failure.
 *
 * The console holds two open streams — the gate's decisions and the merchant's
 * orders — for as long as it is on screen. Every surface switch and the final
 * close abort them, and Chromium reports each as `net::ERR_ABORTED`, so the
 * first clean take was rejected for having worked. A stream that genuinely
 * fails to connect is still caught: it arrives as a 4xx or 5xx response, or as
 * a different errorText, and both are still problems.
 */
const abortedStream = (r) =>
  /\/(v1\/stream|decisions\/stream)$/.test(r.url())
  && r.failure()?.errorText === "net::ERR_ABORTED";

page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => {
  if (!abortedStream(r)) problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`);
});
page.on("response", (r) => { if (r.status() >= 400) problems.push(`http ${r.status()}: ${r.url()}`); });

/**
 * A caption strip along the bottom.
 *
 * Injected into the page rather than added afterwards in an editor, because
 * there is no editor in this pipeline and a backup video plays without the
 * presenter's narration exactly when the presenter is dealing with whatever
 * broke. It says what the beat is meant to show, not what happens next.
 */
async function caption(text) {
  if (!captions) return;
  await page.evaluate((label) => {
    let bar = document.getElementById("__pact_caption");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "__pact_caption";
      Object.assign(bar.style, {
        position: "fixed", left: "0", right: "0", bottom: "0", zIndex: "99999",
        padding: "10px 18px", font: "500 15px/1.4 Inter, system-ui, sans-serif",
        letterSpacing: "0.02em", color: "#f8fafc",
        background: "rgba(9, 12, 20, 0.88)",
        borderTop: "1px solid rgba(148, 163, 184, 0.35)",
      });
      document.body.appendChild(bar);
    }
    bar.textContent = label;
  }, text);
}

const timings = [];
async function phase(label, body) {
  await caption(label);
  const started = Date.now();
  await body();
  const seconds = (Date.now() - started) / 1000;
  timings.push({ label, seconds });
  console.log(`  ${seconds.toFixed(1).padStart(5)}s  ${label}`);
}

async function surface(name) {
  await page.getByRole("button", { name, exact: true }).click();
  await page.waitForTimeout(900);
}

/** Press a beat key and wait for the beat to actually finish.
 *
 *  Same signal browser-check.mjs uses: the beat buttons disable themselves
 *  while one is running, so a fixed sleep is never needed and never right — the
 *  saga step delay is 0.05s here and 0.35s in the container. */
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
  // Long enough for the feed to settle and be read on screen. This is the one
  // place a fixed wait is right: it is stage pacing, not synchronisation.
  await page.waitForTimeout(2200);
}

console.log(`recording ${base} → ${out}\n`);

await page.goto(base, { waitUntil: "networkidle" });

await phase("The console, cold. Nothing is typed after this point.", async () => {
  await surface("Merchant console");
  await page.keyboard.press("0");            // reset: gate, merchant and wallet
  await page.waitForTimeout(1800);
});

await phase("A human grants spending authority, and signs it on this device.", async () => {
  await surface("Grant");
  await page.getByRole("button", { name: /Grant and sign/i }).click();
  // Signing hands the mandate to the app, which moves straight to checkout —
  // so waiting for the signature block on the grant screen times out even
  // though everything worked. The mandate chip in the header is the durable
  // proof that a real signed mandate exists, and it is on screen from here to
  // the end of the demo.
  await page.waitForFunction(
    () => /mnd_/.test(document.querySelector("header")?.innerText ?? ""),
    null,
    { timeout: 30_000 },
  );
  await page.waitForTimeout(2500);
});

await phase("The agent shops against that mandate. The merchant prices it, the gate decides.", async () => {
  await surface("Checkout");   // already here — signing navigates. Explicit anyway.
  const composer = page.getByPlaceholder("What should the agent buy?");
  if (await composer.count()) {
    await composer.fill("restock the office: notebooks and pens");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await page.waitForTimeout(6000);
  }
});

await surface("Merchant console");
await page.waitForTimeout(800);

const BEATS = {
  1: "Beat 1 — an agent discovers the merchant cold and pays. Eight checks, milliseconds.",
  2: "Beat 2 — the merchant reads the buyer's headroom before it offers. The upsell lands.",
  3: "Beat 3 — the same offer made blind. The gate rejects it: CEILING_PER_TXN.",
  4: "Beat 4 — four attacks, four blocks, each with a machine-readable reason.",
  5: "Beat 5 — the payment succeeds and fulfilment fails. Refund, budget released, sale recovered.",
  6: "Beat 6 — the rail delivers the same webhook twice. The second one does nothing.",
};
for (const n of [1, 2, 3, 4, 5, 6]) {
  await phase(BEATS[n], () => beat(Number(n)));
}

await phase("The audit trail: every decision, every saga step, in order.", async () => {
  await page.waitForTimeout(3000);
});

await phase("Six slides, including the one about what is not verified.", async () => {
  await surface("Pitch");
  for (let i = 0; i < 6; i += 1) {
    await page.waitForTimeout(2600);
    if (i < 5) await page.keyboard.press("ArrowRight");
  }
});

await caption("");
await page.waitForTimeout(600);

await context.close();   // the video is only written on context close
await browser.close();

// Playwright names the file after the page's guid. Rename it to something a
// person looking for the backup video on a laptop at the venue will recognise.
const recorded = readdirSync(out).filter((f) => f.endsWith(".webm"));
let saved = null;
if (recorded.length) {
  const newest = recorded
    .map((f) => ({ f, at: statSync(join(out, f)).mtimeMs }))
    .sort((a, b) => b.at - a.at)[0].f;
  saved = join(out, "pact-demo.webm");
  renameSync(join(out, newest), saved);
}

const total = timings.reduce((sum, t) => sum + t.seconds, 0);
console.log(`\n  ${total.toFixed(1)}s total, ${timings.length} phases`);
if (saved) {
  console.log(`  ${saved} — ${(statSync(saved).size / 1_048_576).toFixed(1)} MB`);
}

if (problems.length) {
  console.error(`\n${problems.length} problem(s) — this take is NOT usable as a backup:`);
  for (const p of problems) console.error(`  ${p}`);
  process.exit(1);
}
console.log("\nclean take: no console errors, no failed requests, every beat finished");
