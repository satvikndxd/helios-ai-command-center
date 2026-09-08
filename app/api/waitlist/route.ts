import { NextRequest, NextResponse } from "next/server";
import { sendWaitlistNotification } from "@/lib/mailer";

export const dynamic = "force-dynamic";

// In-memory sliding window for instance-level rate bounding
interface RateRecord {
  count: number;
  resetAt: number;
}
const rateLimitMap = new Map<string, RateRecord>();
const RATE_LIMIT_MAX = 5;
const RATE_LIMIT_WINDOW_MS = 10 * 60 * 1000; // 10 minutes

function cleanupRateLimit() {
  const now = Date.now();
  rateLimitMap.forEach((record, key) => {
    if (record.resetAt <= now) {
      rateLimitMap.delete(key);
    }
  });
}

function generateRequestId(): string {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  const p1 =
    chars.charAt(Math.floor(Math.random() * chars.length)) +
    chars.charAt(Math.floor(Math.random() * chars.length));
  const num = Math.floor(100 + Math.random() * 900);
  return `HX-${p1}-${num}`;
}

/**
 * Pragmatic email validation:
 * - Reject control chars
 * - Trim & check bounds (5..254 chars)
 * - Standard structure check
 */
function validateEmail(raw: unknown): { valid: boolean; normalized?: string; reason?: string } {
  if (typeof raw !== "string") {
    return { valid: false, reason: "INPUT_TYPE_INVALID" };
  }

  const trimmed = raw.trim();

  // Control characters check (\x00-\x1F, \x7F)
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1F\x7F]/.test(trimmed)) {
    return { valid: false, reason: "CONTROL_CHARACTERS_DETECTED" };
  }

  if (trimmed.length < 5 || trimmed.length > 254) {
    return { valid: false, reason: "LENGTH_OUT_OF_BOUNDS" };
  }

  const emailRegex = /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;
  if (!emailRegex.test(trimmed)) {
    return { valid: false, reason: "MALFORMED_SYNTAX" };
  }

  const [, domain] = trimmed.split("@");
  if (!domain || !domain.includes(".")) {
    return { valid: false, reason: "INVALID_DOMAIN" };
  }

  const tld = domain.split(".").pop();
  if (!tld || tld.length < 2) {
    return { valid: false, reason: "INVALID_TLD" };
  }

  return { valid: true, normalized: trimmed.toLowerCase() };
}

function sanitizeText(raw: unknown, maxLen = 120): string {
  if (typeof raw !== "string") return "Not Specified";
  const cleaned = raw.replace(/[\x00-\x1F\x7F<>]/g, "").trim();
  return cleaned.slice(0, maxLen) || "Not Specified";
}

export async function POST(req: NextRequest) {
  const startTime = Date.now();
  const requestId = generateRequestId();
  const nowIso = new Date().toISOString();

  try {
    // 1. Client IP determination for rate bounding
    const forwarded = req.headers.get("x-forwarded-for");
    const ip = forwarded ? forwarded.split(",")[0].trim() : "127.0.0.1";

    cleanupRateLimit();

    const currentRecord = rateLimitMap.get(ip);
    if (currentRecord) {
      if (currentRecord.resetAt > Date.now()) {
        if (currentRecord.count >= RATE_LIMIT_MAX) {
          console.warn(
            JSON.stringify({
              event: "WAITLIST_RATE_LIMITED",
              requestId,
              ipPrefix: ip.substring(0, Math.min(ip.length, 7)) + "***",
              status: 429,
              timestamp: nowIso,
              latencyMs: Date.now() - startTime,
            })
          );
          return NextResponse.json(
            {
              success: false,
              status: 429,
              detail: "REQUEST RATE BOUNDED. RETRY IN A FEW MINUTES.",
              requestId,
            },
            { status: 429 }
          );
        }
        currentRecord.count += 1;
      } else {
        rateLimitMap.set(ip, {
          count: 1,
          resetAt: Date.now() + RATE_LIMIT_WINDOW_MS,
        });
      }
    } else {
      rateLimitMap.set(ip, {
        count: 1,
        resetAt: Date.now() + RATE_LIMIT_WINDOW_MS,
      });
    }

    // 2. Request body parsing
    let body: Record<string, unknown>;
    try {
      body = await req.json();
    } catch {
      return NextResponse.json(
        {
          success: false,
          status: 400,
          detail: "MALFORMED REQUEST PAYLOAD",
          requestId,
        },
        { status: 400 }
      );
    }

    // 3. Honeypot check
    if (body.hp_auth_token || body.hp_firmware_id) {
      console.warn(
        JSON.stringify({
          event: "WAITLIST_HONEYPOT_TRIPPED",
          requestId,
          timestamp: nowIso,
          latencyMs: Date.now() - startTime,
        })
      );
      return NextResponse.json({
        success: true,
        requestId,
        status: "LOGGED",
        channel: "HELIOS INTAKE",
        message: "Your request has been recorded. Await further instruction.",
      });
    }

    // 4. Server-side validation
    const validation = validateEmail(body.email);
    if (!validation.valid || !validation.normalized) {
      return NextResponse.json(
        {
          success: false,
          status: 422,
          detail: "VALID EMAIL REQUIRED",
          reason: validation.reason,
          requestId,
        },
        { status: 422 }
      );
    }

    const industry = sanitizeText(body.industry, 80);
    const usecase = sanitizeText(body.usecase, 300);

    // 5. Server-side mailer dispatch
    const mailResult = await sendWaitlistNotification({
      email: validation.normalized,
      industry,
      usecase,
      requestId,
      timestamp: nowIso,
      source: typeof body.source === "string" ? body.source.slice(0, 50) : undefined,
    });

    // 6. Privacy-first structured audit log (NEVER log raw email)
    console.log(
      JSON.stringify({
        event: "WAITLIST_REQUEST_ACCEPTED",
        requestId,
        provider: mailResult.provider,
        status: 200,
        timestamp: nowIso,
        latencyMs: Date.now() - startTime,
      })
    );

    return NextResponse.json({
      success: true,
      requestId,
      status: "LOGGED",
      channel: "HELIOS INTAKE",
      message: "Your request has been recorded. Await further instruction.",
    });
  } catch (error: unknown) {
    const errorMsg = error instanceof Error ? error.message : "SERVER_FAULT";
    console.error(
      JSON.stringify({
        event: "WAITLIST_UNHANDLED_FAULT",
        requestId,
        errorClass: errorMsg,
        status: 500,
        timestamp: nowIso,
        latencyMs: Date.now() - startTime,
      })
    );
    return NextResponse.json(
      {
        success: false,
        status: 500,
        detail: "INTERNAL INTAKE EXCEPTION",
        requestId,
      },
      { status: 500 }
    );
  }
}
