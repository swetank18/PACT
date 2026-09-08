/** Screenshots each caption page with a transparent background, for ffmpeg to overlay. */
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
import { readdirSync } from "node:fs";
import { resolve } from "node:path";

const dir = resolve(process.argv[2] ?? "./assets/overlays");
const browser = await chromium.launch({ args: ["--no-sandbox", "--disable-gpu"] });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
for (const f of readdirSync(dir).filter((f) => f.endsWith(".html"))) {
  await page.goto(`file://${dir}/${f}`, { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  await page.screenshot({ path: `${dir}/${f.replace(".html", ".png")}`, omitBackground: true });
  console.log("overlay", f.replace(".html", ".png"));
}
await browser.close();
