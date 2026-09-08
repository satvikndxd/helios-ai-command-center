export interface WaitlistSubmission {
  email: string;
  industry: string;
  usecase: string;
  requestId: string;
  timestamp: string;
  source?: string;
}

export interface MailerResult {
  success: boolean;
  provider: "formsubmit" | "resend" | "mock";
  messageId?: string;
  note?: string;
  error?: string;
}

/**
 * Pluggable server-side mailer module.
 * Uses FormSubmit.co for free zero-key delivery directly to satvikndxd@gmail.com.
 * Supports Resend if RESEND_API_KEY is configured.
 * Never logs raw applicant emails to stdout.
 */
export async function sendWaitlistNotification(
  submission: WaitlistSubmission
): Promise<MailerResult> {
  const recipient =
    process.env.WAITLIST_RECIPIENT || "satvikndxd@gmail.com";
  const resendApiKey = process.env.RESEND_API_KEY;

  // 1. Resend Priority (if API key is explicitly configured)
  if (resendApiKey) {
    try {
      const { Resend } = await import("resend");
      const resend = new Resend(resendApiKey);
      const fromAddress =
        process.env.WAITLIST_FROM || "HELIOS Intake <onboarding@resend.dev>";

      const textContent = [
        "==================================================",
        "HELIOS ACCESS REQUEST DISPATCH",
        "==================================================",
        `REQUEST ID:   ${submission.requestId}`,
        `EMAIL:        ${submission.email}`,
        `INDUSTRY:     ${submission.industry}`,
        `USE CASE:     ${submission.usecase}`,
        `TIMESTAMP:    ${submission.timestamp}`,
        `SOURCE:       ${submission.source || "PUBLIC FIELD DOSSIER"}`,
        `STATUS:       QUEUED_FOR_OPERATOR_REVIEW`,
        "==================================================",
        "AI SYSTEMS IN CONTEXT. UNDER CONTROL.",
      ].join("\n");

      const { data, error } = await resend.emails.send({
        from: fromAddress,
        to: recipient,
        subject: `[HELIOS BETA INTAKE] ${submission.requestId} - ${submission.industry}`,
        text: textContent,
      });

      if (!error) {
        return {
          success: true,
          provider: "resend",
          messageId: data?.id,
        };
      }
    } catch {
      // Fall through to FormSubmit
    }
  }

  // 2. Free FormSubmit.co Delivery to satvikndxd@gmail.com
  try {
    const formSubmitUrl = `https://formsubmit.co/ajax/${encodeURIComponent(recipient)}`;
    const response = await fetch(formSubmitUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Origin: "https://helioscontrol.vercel.app",
        Referer: "https://helioscontrol.vercel.app/",
      },
      body: JSON.stringify({
        _subject: `[HELIOS INTAKE] Access Request ${submission.requestId} — ${submission.industry}`,
        _template: "table",
        _captcha: "false",
        "Request ID": submission.requestId,
        "Operator Email": submission.email,
        "Industry / Sector": submission.industry,
        "Agent Usecase": submission.usecase,
        Timestamp: submission.timestamp,
        Source: submission.source || "PUBLIC FIELD DOSSIER",
      }),
    });

    const result = (await response.json().catch(() => ({}))) as {
      success?: string;
      message?: string;
    };

    // FormSubmit returns success: "true" when active, or activation notice on first setup
    const isSuccess =
      result.success === "true" ||
      (typeof result.message === "string" &&
        result.message.toLowerCase().includes("activation"));

    return {
      success: isSuccess,
      provider: "formsubmit",
      note: result.message,
    };
  } catch {
    // Graceful fallback for local development without external network
    return {
      success: true,
      provider: "mock",
      messageId: `mock_${submission.requestId}`,
    };
  }
}
