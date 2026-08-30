/**
 * Ed25519 on the buyer's device, plus the base64url helpers everything else
 * uses.
 *
 * @noble/ed25519 rather than crypto.subtle: Ed25519 support in WebCrypto is
 * still uneven across browsers and a signature that fails on the demo laptop's
 * browser is not a risk worth taking on stage.
 */
import * as ed from "@noble/ed25519";
import { sha512 } from "@noble/hashes/sha512";

import { canonicalize, type JsonValue } from "./jcs";

// @noble/ed25519 v2 keeps hashing pluggable so the library stays dependency
// free. Wire BOTH hooks to @noble/hashes.
//
// Wiring only sha512Sync would leave the async API — which is the one this
// module actually calls — routed through crypto.subtle.digest for SHA-512.
// That reintroduces the WebCrypto dependency this file exists to avoid, and it
// breaks outright in any environment where the Uint8Array crosses a realm
// boundary, because subtle.digest's argument check is a cross-realm instanceof.
// One extra line removes WebCrypto from the signing path entirely.
ed.etc.sha512Sync = (...m) => sha512(ed.etc.concatBytes(...m));
ed.etc.sha512Async = async (...m) => sha512(ed.etc.concatBytes(...m));

export function b64uEncode(raw: Uint8Array): string {
  let bin = "";
  for (const byte of raw) bin += String.fromCharCode(byte);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64uDecode(s: string): Uint8Array {
  const padded = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(padded);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function bytesToHex(raw: Uint8Array): string {
  return Array.from(raw, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export type KeyPair = { privateKey: Uint8Array; publicKey: Uint8Array };

export async function generateKeyPair(): Promise<KeyPair> {
  const privateKey = ed.utils.randomPrivateKey();
  return { privateKey, publicKey: await ed.getPublicKeyAsync(privateKey) };
}

export async function publicKeyFor(privateKey: Uint8Array): Promise<Uint8Array> {
  return ed.getPublicKeyAsync(privateKey);
}

/**
 * The signing procedure from 00-SHARED-CONTRACTS section 6, and the only place
 * in the console that is allowed to define it:
 *
 *   strip `signature` -> RFC 8785 JCS -> Ed25519 -> base64url unpadded
 */
export async function signPayload(
  payload: Record<string, JsonValue>,
  privateKey: Uint8Array,
): Promise<string> {
  const { signature: _dropped, ...unsigned } = payload;
  const sig = await ed.signAsync(canonicalize(unsigned as JsonValue), privateKey);
  return b64uEncode(sig);
}

export async function verifyPayload(
  payload: Record<string, JsonValue>,
  signatureB64u: string,
  publicKey: Uint8Array,
): Promise<boolean> {
  const { signature: _dropped, ...unsigned } = payload;
  try {
    return await ed.verifyAsync(
      b64uDecode(signatureB64u),
      canonicalize(unsigned as JsonValue),
      publicKey,
    );
  } catch {
    return false;
  }
}
