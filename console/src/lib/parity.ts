/**
 * The parity check, run in the browser at boot.
 *
 * test/parity.test.ts proves the same thing under Node, which is where CI can
 * see it. This runs the identical assertions in the actual runtime the demo
 * uses — a real browser, a real WASM-free Ed25519, the real TextEncoder — and
 * puts the result on screen.
 *
 * It is cheap (two signatures) and it is the one claim in the product a judge
 * can check by looking. If it ever goes red on the demo laptop, that is worth
 * knowing before the pitch rather than during it.
 */
import { b64uDecode, bytesToHex, hexToBytes, publicKeyFor, signPayload } from "./crypto";
import { canonicalizeToString } from "./jcs";

export type ParityState =
  | { status: "checking" }
  | { status: "ok"; cases: number }
  | { status: "failed"; detail: string }
  | { status: "unavailable"; detail: string };

type Vector = {
  keys: Record<string, { seed_hex: string; public_key_b64u: string }>;
  cases: Array<{
    name: string;
    signer: string;
    payload: Record<string, unknown>;
    canonical: string;
    signature: string;
  }>;
};

/** Served by the vite middleware from the shared fixtures/ directory. */
const VECTOR_URL = "/fixtures/keys/test_vector.json";

export async function checkParity(): Promise<ParityState> {
  let vector: Vector;
  try {
    const res = await fetch(VECTOR_URL);
    if (!res.ok) throw new Error(`${res.status}`);
    vector = (await res.json()) as Vector;
  } catch (e) {
    // A built bundle served without the middleware has no fixtures directory.
    // That is not a parity failure and must not be reported as one.
    return {
      status: "unavailable",
      detail: `test vector not served at ${VECTOR_URL} (${e instanceof Error ? e.message : e})`,
    };
  }

  try {
    for (const [name, k] of Object.entries(vector.keys)) {
      const pub = await publicKeyFor(hexToBytes(k.seed_hex));
      if (bytesToHex(pub) !== bytesToHex(b64uDecode(k.public_key_b64u))) {
        return { status: "failed", detail: `public key mismatch for ${name}` };
      }
    }

    for (const c of vector.cases) {
      const { signature: _drop, ...unsigned } = c.payload;
      if (canonicalizeToString(unsigned as never) !== c.canonical) {
        return { status: "failed", detail: `canonical form differs on case "${c.name}"` };
      }
      const sig = await signPayload(c.payload as never, hexToBytes(vector.keys[c.signer].seed_hex));
      if (sig !== c.signature) {
        return { status: "failed", detail: `signature differs on case "${c.name}"` };
      }
    }

    return { status: "ok", cases: vector.cases.length };
  } catch (e) {
    return { status: "failed", detail: e instanceof Error ? e.message : String(e) };
  }
}
