/**
 * The buyer's device key.
 *
 * This is the piece of real cryptography Lane C owns. The private key is
 * generated in the browser, stored in localStorage, and never sent anywhere.
 * The agent receives a signed mandate, not a key, which is the entire reason
 * the agent cannot spend outside what the human granted.
 *
 * localStorage is not a secure enclave and we say so on screen rather than
 * implying otherwise. On a real phone this is the device keystore behind a
 * biometric prompt; the security model is the same shape, the storage is not.
 */
import { b64uEncode, bytesToHex, generateKeyPair, hexToBytes, publicKeyFor } from "./crypto";

const STORAGE_KEY = "pact.device_key.v1";

export type DeviceKey = {
  privateKey: Uint8Array;
  publicKey: Uint8Array;
  publicKeyB64u: string;
};

let cached: DeviceKey | null = null;

async function fromSeedHex(hex: string): Promise<DeviceKey> {
  const privateKey = hexToBytes(hex);
  const publicKey = await publicKeyFor(privateKey);
  return { privateKey, publicKey, publicKeyB64u: b64uEncode(publicKey) };
}

/** Loads the device key, generating one on first use. */
export async function getDeviceKey(): Promise<DeviceKey> {
  if (cached) return cached;

  let stored: string | null = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private window, or site data blocked. Fall through to an ephemeral key:
    // the demo still works, it just does not survive a reload.
  }

  if (stored && /^[0-9a-f]{64}$/.test(stored)) {
    cached = await fromSeedHex(stored);
    return cached;
  }

  const pair = await generateKeyPair();
  try {
    localStorage.setItem(STORAGE_KEY, bytesToHex(pair.privateKey));
  } catch {
    /* ephemeral is fine */
  }
  cached = { ...pair, publicKeyB64u: b64uEncode(pair.publicKey) };
  return cached;
}

/** Used by the reset control so a fresh run starts from a fresh device. */
export async function rotateDeviceKey(): Promise<DeviceKey> {
  cached = null;
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  return getDeviceKey();
}

/* ------------------------------------------------------------------ ids --- */

const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

/**
 * Prefixed, sortable, ULID-shaped ids. Section 5 of the shared contract fixes
 * the prefixes: mnd_, dec_, qte_, ord_, stl_, sim_.
 */
export function newId(prefix: "mnd" | "dec" | "qte" | "ord" | "stl" | "sim"): string {
  const bytes = new Uint8Array(6);
  crypto.getRandomValues(bytes);
  let ts = Date.now();
  let time = "";
  for (let i = 0; i < 6; i++) {
    time = CROCKFORD[ts % 32] + time;
    ts = Math.floor(ts / 32);
  }
  let rand = "";
  for (const b of bytes) rand += CROCKFORD[b % 32];
  return `${prefix}_${time}${rand}`;
}
