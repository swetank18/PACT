/**
 * THE BLOCKING GATE. LANE-C section 1.
 *
 * A signature produced in this codebase must verify inside Lane A's engine.
 * The vector in fixtures/keys/test_vector.json was produced by an independent
 * Python implementation, so byte equality here means the two languages agree
 * on canonicalisation, on the signing procedure, and on the encoding.
 *
 * If this test goes red, nothing else in Lane C matters. Fix it first.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { canonicalizeToString } from "../src/lib/jcs";
import {
  b64uDecode,
  bytesToHex,
  hexToBytes,
  publicKeyFor,
  signPayload,
  verifyPayload,
} from "../src/lib/crypto";

type Vector = {
  algorithm: string;
  canonicalization: string;
  encoding: string;
  keys: Record<string, { seed_hex: string; public_key_b64u: string }>;
  cases: Array<{
    name: string;
    signer: string;
    payload: Record<string, any>;
    canonical: string;
    signature: string;
  }>;
};

const vector: Vector = JSON.parse(
  readFileSync(resolve(__dirname, "../../fixtures/keys/test_vector.json"), "utf-8"),
);

describe("cross language signature parity", () => {
  it("uses the algorithms the contract froze", () => {
    expect(vector.algorithm).toBe("Ed25519");
    expect(vector.canonicalization).toBe("RFC8785");
    expect(vector.encoding).toBe("base64url-unpadded");
  });

  it("derives the same public key from the same seed", async () => {
    for (const [name, k] of Object.entries(vector.keys)) {
      const pub = await publicKeyFor(hexToBytes(k.seed_hex));
      expect(bytesToHex(pub), `public key for ${name}`).toBe(
        bytesToHex(b64uDecode(k.public_key_b64u)),
      );
    }
  });

  for (const c of vector.cases) {
    describe(`case: ${c.name}`, () => {
      it("canonicalises byte for byte", () => {
        const { signature: _drop, ...unsigned } = c.payload;
        expect(canonicalizeToString(unsigned)).toBe(c.canonical);
      });

      it("produces the same signature byte for byte", async () => {
        const seed = hexToBytes(vector.keys[c.signer].seed_hex);
        expect(await signPayload(c.payload, seed)).toBe(c.signature);
      });

      it("verifies the signature the other language produced", async () => {
        const pub = b64uDecode(vector.keys[c.signer].public_key_b64u);
        expect(await verifyPayload(c.payload, c.signature, pub)).toBe(true);
      });

      it("rejects a tampered payload", async () => {
        const pub = b64uDecode(vector.keys[c.signer].public_key_b64u);
        const tampered = { ...c.payload, __injected: "extra field" };
        expect(await verifyPayload(tampered, c.signature, pub)).toBe(false);
      });
    });
  }
});

describe("JCS edge cases the RFC calls out", () => {
  it("sorts keys by UTF-16 code unit, not by locale", () => {
    // Locale-aware sorting would put "a" before "A"; the RFC does not.
    expect(canonicalizeToString({ b: 1, a: 2, A: 3, "é": 4, z: 5 })).toBe(
      '{"A":3,"a":2,"b":1,"z":5,"é":4}',
    );
  });

  it("emits the short escapes and \\u00xx for other control characters", () => {
    const input = { k: "a\tb\nc\u0001d\\e\"f" };
    expect(canonicalizeToString(input)).toBe('{"k":"a\\tb\\nc\\u0001d\\\\e\\"f"}');
  });

  it("normalises negative zero", () => {
    expect(canonicalizeToString({ n: -0 })).toBe('{"n":0}');
  });

  it("keeps array order but sorts nested object keys", () => {
    expect(
      canonicalizeToString([
        { b: 1, a: 2 },
        { d: 3, c: 4 },
      ]),
    ).toBe('[{"a":2,"b":1},{"c":4,"d":3}]');
  });

  it("round trips through the signer for a payload with a signature already on it", async () => {
    const seed = hexToBytes(vector.keys.delegator.seed_hex);
    const payload = { a: 1, b: "two" };
    const sig = await signPayload(payload, seed);
    // Signing again with the signature attached must produce the same result,
    // because the procedure strips it first.
    expect(await signPayload({ ...payload, signature: sig }, seed)).toBe(sig);
  });
});
