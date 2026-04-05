import { cookies } from "next/headers";
import crypto from "crypto";

import { env } from "@/env.js";

// Admin credentials from environment (with defaults)
const ADMIN_EMAIL = env.ADMIN_EMAIL;
const ADMIN_PASSWORD = env.ADMIN_PASSWORD;

// Session cookie configuration
const SESSION_COOKIE_NAME = "session";
const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  maxAge: 60 * 60 * 24 * 7, // 7 days
  path: "/",
};

// Cache the secret key to avoid re-hashing on every request
let cachedSecretKey: Buffer | null = null;

const getSecretKey = (): Buffer => {
  if (cachedSecretKey) return cachedSecretKey;
  const secret = process.env.BETTER_AUTH_SECRET;
  if (!secret) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("BETTER_AUTH_SECRET must be set in production");
    }
    const devSecret = "deer-flow-default-secret-key";
    cachedSecretKey = crypto.createHash("sha256").update(devSecret).digest();
    return cachedSecretKey;
  }
  cachedSecretKey = crypto.createHash("sha256").update(secret).digest();
  return cachedSecretKey;
};

interface SessionData {
  email: string;
  iat: number;
  exp: number;
}

function encodeBase64(data: object): string {
  return Buffer.from(JSON.stringify(data)).toString("base64url");
}

function createSignature(data: string, key: Buffer): string {
  return crypto.createHmac("sha256", key).update(data).digest("base64url");
}

export async function createSession(email: string): Promise<string> {
  const key = getSecretKey();
  const now = Math.floor(Date.now() / 1000);
  const payload: SessionData = {
    email,
    iat: now,
    exp: now + SESSION_COOKIE_OPTIONS.maxAge,
  };

  const header = encodeBase64({ alg: "HS256", typ: "JWT" });
  const payloadEncoded = encodeBase64(payload);
  const signature = createSignature(`${header}.${payloadEncoded}`, key);

  return `${header}.${payloadEncoded}.${signature}`;
}

export async function setSessionCookie(token: string) {
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE_NAME, token, SESSION_COOKIE_OPTIONS);
}

export function validateCredentials(email: string, password: string): boolean {
  return email === ADMIN_EMAIL && password === ADMIN_PASSWORD;
}
