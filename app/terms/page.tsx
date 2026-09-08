import React from "react";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Terms of Use — HELIOS",
  description:
    "Terms of use governing the HELIOS public website and beta access waitlist.",
};

export default function TermsPage() {
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
            <div>DIRECTIVE // HL-TERMS-001</div>
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
            TERMS OF USE
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
            BETA WAITLIST &amp; PUBLIC SITE DIRECTIVE
          </div>

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
                01. SCOPE &amp; ACCEPTANCE
              </h2>
              <p>
                These Terms of Use govern access to and use of this public
                website and the HELIOS beta waitlist. By visiting this website or
                submitting an email to request beta access, you agree to comply
                with these terms. If you do not agree, do not submit your email
                or interact with the waitlist intake.
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
                02. NATURE OF THE BETA WAITLIST
              </h2>
              <p>
                The waitlist is an intake mechanism for software developers and
                teams building autonomous AI agents. Submitting an email:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>Does not create an account, license, or contractual entitlement.</li>
                <li>Does not guarantee selection or admission into the beta program.</li>
                <li>Does not obligate HELIOS to provide software, support, or service availability.</li>
              </ul>
              <p style={{ marginTop: "8px" }}>
                HELIOS reserves the right to accept, defer, or decline access
                requests at its sole operational discretion.
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
                03. ACCEPTABLE USE &amp; PROHIBITED CONDUCT
              </h2>
              <p>
                You agree not to misuse this website or its intake endpoints.
                Prohibited actions include:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>Submitting fraudulent, falsified, or third-party email addresses without consent.</li>
                <li>Deploying automated scrapers, flood bots, or denial-of-service payloads against the intake API.</li>
                <li>Attempting to bypass security headers, rate limiters, or honeypot defenses.</li>
                <li>Introducing malicious code, command injections, or header injections.</li>
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
                04. INTELLECTUAL PROPERTY
              </h2>
              <p>
                All trademarks, visual identity systems, equipment plate marks,
                insignia, documentation, and source code associated with HELIOS
                are the intellectual property of the operator and respective
                contributors under the repository&apos;s open-source license
                (MIT).
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
                05. DISCLAIMER OF WARRANTIES &amp; LIMITATION OF LIABILITY
              </h2>
              <p>
                This website and its waitlist service are provided &ldquo;as
                is&rdquo; without warranties of any kind, express or implied. To
                the maximum extent permitted by applicable law, the operator
                shall not be liable for any indirect, incidental, special, or
                consequential damages resulting from website downtime, intake
                errors, or lack of beta admission.
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
                06. MODIFICATIONS &amp; INQUIRIES
              </h2>
              <p>
                We may modify these terms at any time by updating this document.
                For legal notices or questions regarding these terms, contact{" "}
                <a
                  href="mailto:satvikndxd@gmail.com"
                  style={{ color: "var(--color-ink)", fontWeight: 700 }}
                >
                  satvikndxd@gmail.com
                </a>
                .
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
            <span>DOCUMENT // HL-TERMS-001</span>
            <span>LAST REVISED: SEPTEMBER 2026</span>
            <span>HELIOS SYSTEMS</span>
          </div>
        </div>
      </div>
    </div>
  );
}
