# Screenshots

Taken by `console/browser-check.mjs` against the single-port build, in Chromium,
on 2026-08-31. Before this, nobody had seen the console rendered: it was covered
by 32 tests including a full jsdom mount, and driven end to end through its own
proxy, but no browser had ever opened it.

Opening one immediately found a bug. `SIGNATURE PARITY · 2 VECTORS` in the
header is the console's one visible, checkable claim — it verifies in the
browser that Ed25519 and RFC 8785 agree with Python's byte for byte. It was
missing, because the test vector is served by a Vite dev-server middleware that
does not exist in a built bundle. It degraded to "unavailable" rather than
claiming a failure, which is correct behaviour and exactly why no test caught
it.

| File | Surface |
| --- | --- |
| `merchant-console.png` | GMV, live orders, gate decisions, the audit trail mid-recovery |
| `grant.png` | Granting authority: the mandate the buyer signs |
| `checkout.png` | The empty state, with no mandate on the device |
| `pitch.png` | The slides |

Regenerate:

```bash
docker compose up -d                                    # or ./scripts/dev.sh
cd console && npm run browser -- http://localhost:8080 ../docs/screenshots
```

CI runs the same script against the container on every push and uploads the
result, so a surface that stops rendering fails a build.
