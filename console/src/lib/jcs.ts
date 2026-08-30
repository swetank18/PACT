/**
 * RFC 8785 JSON Canonicalization Scheme.
 *
 * Written from the RFC, not ported from the Python side, because parity only
 * proves something if the two implementations are independent.
 *
 * Two traps this avoids, both called out in LANE-C section 1:
 *   - JSON.stringify with sorted keys is NOT JCS. It sorts by whatever the
 *     caller's comparator does and it does not normalise numbers.
 *   - Key ordering is by UTF-16 code unit. JavaScript's default string sort
 *     already compares UTF-16 code units, which is the one place the platform
 *     happens to agree with the RFC. Do not "improve" it with localeCompare.
 */

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [k: string]: JsonValue };

// RFC 8785 §3.2.2.2
const SHORT_ESCAPES: Record<number, string> = {
  0x08: "\\b",
  0x09: "\\t",
  0x0a: "\\n",
  0x0c: "\\f",
  0x0d: "\\r",
  0x22: '\\"',
  0x5c: "\\\\",
};

function escapeString(s: string): string {
  let out = '"';
  for (const ch of s) {
    const cp = ch.codePointAt(0)!;
    const short = SHORT_ESCAPES[cp];
    if (short !== undefined) out += short;
    else if (cp < 0x20) out += "\\u" + cp.toString(16).padStart(4, "0");
    else out += ch;
  }
  return out + '"';
}

function serializeNumber(n: number): string {
  if (!Number.isFinite(n)) throw new Error("NaN and Infinity are not valid JSON");
  // RFC 8785 §3.2.2.3: ES6 Number::toString, with -0 normalised to 0.
  if (n === 0) return "0";
  return String(n);
}

function ser(v: JsonValue, out: string[]): void {
  if (v === null) {
    out.push("null");
  } else if (typeof v === "boolean") {
    out.push(v ? "true" : "false");
  } else if (typeof v === "number") {
    out.push(serializeNumber(v));
  } else if (typeof v === "string") {
    out.push(escapeString(v));
  } else if (Array.isArray(v)) {
    out.push("[");
    v.forEach((item, i) => {
      if (i) out.push(",");
      ser(item, out);
    });
    out.push("]");
  } else if (typeof v === "object") {
    out.push("{");
    // Default sort compares UTF-16 code units, which is what the RFC asks for.
    const keys = Object.keys(v).sort();
    keys.forEach((k, i) => {
      if (i) out.push(",");
      out.push(escapeString(k));
      out.push(":");
      ser(v[k], out);
    });
    out.push("}");
  } else {
    throw new Error(`not JSON serialisable: ${typeof v}`);
  }
}

/** Canonical JSON as a string. */
export function canonicalizeToString(value: JsonValue): string {
  const out: string[] = [];
  ser(value, out);
  return out.join("");
}

/** Canonical JSON as UTF-8 bytes. This is what gets signed. */
export function canonicalize(value: JsonValue): Uint8Array {
  return new TextEncoder().encode(canonicalizeToString(value));
}
