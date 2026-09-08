import React from "react";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Privacy Directive — HELIOS",
  description:
    "Technical directive on data handling, minimization, retention, and operator communication for the HELIOS beta waitlist.",
};

export default function PrivacyPage() {
  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: "var(--color-near-black)",
        color: "var(--color-paper)",
        fontFamily: "var(--font-mono)",
        padding: "40px 20px 80px",
      }}
    >
      <div style={{ maxWidth: "880px", margin: "0 auto" }}>
        {/* Nav link back to dossier */}
        <div style={{ marginBottom: "24px" }}>
          <Link
            href="/"
            style={{
              color: "var(--color-signal-green)",
              textDecoration: "none",
              fontSize: "12px",
              letterSpacing: "0.15em",
              display: "inline-flex",
              alignItems: "center",
              gap: "8px",
            }}
          >
            ← RETURN TO FIELD DOSSIER
          </Link>
        </div>

        {/* Header Document Plate */}
        <div
          className="dossier-sheet"
          style={{
            padding: "40px 32px",
            border: "1px solid var(--color-border-paper-strong)",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid var(--color-border-paper-strong)",
              paddingBottom: "16px",
              marginBottom: "28px",
              fontSize: "11px",
              letterSpacing: "0.18em",
              color: "var(--color-muted-ink)",
              flexWrap: "wrap",
              gap: "8px",
            }}
          >
            <div>DIRECTIVE // HL-PRIV-001</div>
            <div>STATUS: ENFORCING</div>
            <div>EFFECTIVE: SEPTEMBER 2026</div>
          </div>

          <h1
            style={{
              fontSize: "clamp(24px, 4vw, 36px)",
              fontWeight: 800,
              letterSpacing: "0.12em",
              color: "var(--color-ink)",
              textTransform: "uppercase",
              lineHeight: 1.2,
              marginBottom: "8px",
            }}
          >
            PRIVACY DIRECTIVE
          </h1>

          <div
            style={{
              fontSize: "12px",
              letterSpacing: "0.15em",
              color: "var(--color-signal-green)",
              fontWeight: 700,
              marginBottom: "28px",
            }}
          >
            <span className="indicator-pip" />
            DATA MINIMIZATION • RETENTION • OPERATIONAL NOTICE
          </div>

          {/* Core Body Sections */}
          <div
            style={{
              fontSize: "13px",
              lineHeight: 1.7,
              color: "var(--color-ink)",
              display: "flex",
              flexDirection: "column",
              gap: "24px",
            }}
          >
            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                01. OPERATOR IDENTITY &amp; SCOPE
              </h2>
              <p>
                HELIOS (&ldquo;we&rdquo;, &ldquo;us&rdquo;, or &ldquo;our&rdquo;)
                is an open-source and developer-focused AI governance project
                operated by Satvik Anand. For all inquiries, data deletion
                requests, or security matters regarding this public website and
                beta waitlist, the primary contact is:
              </p>
              <div
                style={{
                  backgroundColor: "#FFFFFF",
                  border: "1px solid var(--color-border-paper-strong)",
                  padding: "10px 14px",
                  marginTop: "8px",
                  fontSize: "12px",
                }}
              >
                <strong>Operator:</strong> Satvik Anand (HELIOS Maintainer)<br />
                <strong>Direct Contact:</strong> satvikndxd@gmail.com<br />
                <strong>Repository:</strong> github.com/satvikndxd/helios-ai-command-center
              </div>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                02. STRICT DATA MINIMIZATION (WHAT WE COLLECT)
              </h2>
              <p>
                In accordance with principles of data minimization under India&apos;s
                Digital Personal Data Protection Act (DPDP) 2023 / DPDP Rules 2025
                and the European Union General Data Protection Regulation (GDPR),
                this public landing page requests and collects only the minimal
                information required to evaluate and communicate beta access:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>
                  <strong>Operator Email:</strong> Used to communicate waitlist
                  status and admission instructions.
                </li>
                <li>
                  <strong>Industry / Sector:</strong> Used to understand your
                  agent environment (e.g. AI Agent Systems, Cloud Infra, Cybersecurity).
                </li>
                <li>
                  <strong>Agent Use Case:</strong> Brief description of the autonomous
                  AI agent architecture you are governing.
                </li>
              </ul>
              <p style={{ marginTop: "8px" }}>
                We explicitly <strong>DO NOT</strong> collect phone numbers,
                passwords, physical locations, financial accounts, or social
                profiles. We do not enrich your submission with external data.
              </p>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                03. TECHNICAL &amp; SECURITY TELEMETRY
              </h2>
              <p>
                When interacting with the serverless endpoint, ephemeral network
                information is processed purely for operational defense:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>
                  <strong>Client IP Address:</strong> Processed ephemerally in
                  memory for sliding-window rate limiting (maximum 5 requests per
                  10-minute window) to prevent denial-of-service and bot spam. IP
                  addresses are not stored in a persistent database.
                </li>
                <li>
                  <strong>Diagnostic Logs:</strong> Server logs record generated
                  Request IDs, HTTP status codes, latency, and timestamp.{" "}
                  <strong>
                    Raw email addresses are never output to standard log streams.
                  </strong>
                </li>
              </ul>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                04. THIRD-PARTY PROCESSORS
              </h2>
              <p>
                We do not sell, rent, or lease personal information. To operate
                the waitlist dispatch pipeline, the following service providers
                process data solely on our instructions:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>
                  <strong>Vercel Inc.:</strong> Hosting provider and serverless
                  compute environment.
                </li>
                <li>
                  <strong>FormSubmit.co:</strong> Free form processing and email
                  forwarding pipeline used to deliver submissions directly to the
                  operator inbox (satvikndxd@gmail.com).
                </li>
                <li>
                  <strong>Resend Inc.:</strong> Secondary transactional delivery
                  pipeline (if configured).
                </li>
              </ul>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                05. DATA RETENTION &amp; LIFECYCLE
              </h2>
              <p>
                Submitted email addresses are delivered to the operator&apos;s
                secure mailbox (satvikndxd@gmail.com). We retain applicant email
                addresses only for the duration of the beta program evaluation
                period (up to 365 days from submission) or until you request
                deletion. We do not claim automated background database deletion
                because no separate marketing database is maintained.
              </p>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                06. COOKIES &amp; TRACKERS (ZERO-TRACKING POLICY)
              </h2>
              <p>
                This website operates on a <strong>zero-tracking architecture</strong>.
                We do not set advertising cookies, analytics cookies, session
                recording tools (such as Hotjar or FullStory), or behavioral
                retargeting pixels (Meta, Google, TikTok). Because no
                non-essential cookies are utilized, no intrusive consent pop-up
                is required or displayed.
              </p>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                07. USER RIGHTS &amp; DELETION REQUESTS
              </h2>
              <p>
                Regardless of your geographic location, you retain the right to:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>Request confirmation of whether your email address is on file.</li>
                <li>Request immediate and permanent deletion of your submission.</li>
                <li>Withdraw consent for beta access communications.</li>
              </ul>
              <p style={{ marginTop: "8px" }}>
                To exercise any of these rights, email{" "}
                <a
                  href="mailto:satvikndxd@gmail.com?subject=[HELIOS%20PRIVACY%20REQUEST]"
                  style={{ color: "var(--color-ink)", fontWeight: 700 }}
                >
                  satvikndxd@gmail.com
                </a>{" "}
                with the subject line <code>[HELIOS PRIVACY REQUEST]</code>. We
                verify and process deletion requests manually within thirty (30)
                calendar days.
              </p>
            </section>

            <section>
              <h2
                style={{
                  fontSize: "14px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  textTransform: "uppercase",
                  marginBottom: "8px",
                  borderBottom: "1px solid var(--color-border-paper)",
                  paddingBottom: "4px",
                }}
              >
                08. ACCURATE LEGAL DISCLOSURE
              </h2>
              <p>
                We do not make unsubstantiated claims such as &ldquo;100%
                unbreachable security&rdquo; or &ldquo;universally certified
                compliance.&rdquo; Instead, we build HELIOS with defensive,
                privacy-by-default software engineering, transparent operational
                practices, and verifiable control planes.
              </p>
            </section>
          </div>

          <div
            style={{
              marginTop: "36px",
              borderTop: "1px solid var(--color-border-paper)",
              paddingTop: "16px",
              display: "flex",
              justifyContent: "space-between",
              fontSize: "11px",
              color: "var(--color-muted-ink)",
              flexWrap: "wrap",
              gap: "8px",
            }}
          >
            <span>DOCUMENT // HL-PRIV-001</span>
            <span>LAST REVISED: SEPTEMBER 2026</span>
            <span>HELIOS SYSTEMS</span>
          </div>
        </div>
      </div>
    </div>
  );
}
