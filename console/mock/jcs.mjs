/**
 * RFC 8785 JCS for the mock services.
 *
 * A third copy, kept deliberately: the mock has to sign headroom envelopes the
 * same way the real gate will, and importing the TypeScript one would mean
 * building the console before the mock can start.
 */

const SHORT = {
  0x08: "\\b",
  0x09: "\\t",
  0x0a: "\\n",
  0x0c: "\\f",
  0x0d: "\\r",
  0x22: '\\"',
  0x5c: "\\\\",
};

function esc(str) {
  let out = '"';
  for (const ch of str) {
    const cp = ch.codePointAt(0);
    if (SHORT[cp] !== undefined) out += SHORT[cp];
    else if (cp < 0x20) out += "\\u" + cp.toString(16).padStart(4, "0");
    else out += ch;
  }
  return out + '"';
}

function ser(v, out) {
  if (v === null) out.push("null");
  else if (typeof v === "boolean") out.push(v ? "true" : "false");
  else if (typeof v === "number") out.push(v === 0 ? "0" : String(v));
  else if (typeof v === "string") out.push(esc(v));
  else if (Array.isArray(v)) {
    out.push("[");
    v.forEach((x, i) => {
      if (i) out.push(",");
      ser(x, out);
    });
    out.push("]");
  } else {
    out.push("{");
    Object.keys(v)
      .sort()
      .forEach((k, i) => {
        if (i) out.push(",");
        out.push(esc(k), ":");
        ser(v[k], out);
      });
    out.push("}");
  }
}

export function canonicalize(value) {
  const out = [];
  ser(value, out);
  return Buffer.from(out.join(""), "utf-8");
}
