import React from "react";
import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Security Directive — HELIOS",
  description:
    "Vulnerability disclosure guidelines, reporting protocols, and security policy for HELIOS.",
};

export default function SecurityPage() {
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
            <div>DIRECTIVE // HL-SEC-001</div>
            <div>STATUS: ENFORCING</div>
            <div>COORDINATED DISCLOSURE</div>
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
            SECURITY DIRECTIVE
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
            VULNERABILITY DISCLOSURE &amp; DEFENSIVE REPORTING
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
                01. COMMITMENT TO DEFENSIVE INTEGRITY
              </h2>
              <p>
                HELIOS is built to provide control planes for autonomous systems.
                We consider defensibility and transparency foundational. We
                actively encourage responsible reporting of any security
                vulnerabilities or intake flaws identified across our web
                infrastructure and codebase.
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
                02. REPORTING PROTOCOL
              </h2>
              <p>
                If you discover a potential vulnerability, please report it
                directly to the maintainer:
              </p>
              <div
                style={{
                  backgroundColor: "#FFFFFF",
                  border: "1px solid var(--color-border-paper-strong)",
                  padding: "12px 16px",
                  marginTop: "8px",
                  fontSize: "12px",
                }}
              >
                <strong>Security Contact:</strong> satvikndxd@gmail.com<br />
                <strong>Subject Line:</strong> <code>[HELIOS SECURITY DISCLOSURE] &lt;Short Description&gt;</code><br />
                <strong>Security.txt:</strong>{" "}
                <a
                  href="/.well-known/security.txt"
                  style={{ color: "var(--color-ink)", textDecoration: "underline" }}
                >
                  /.well-known/security.txt
                </a>
              </div>
              <p style={{ marginTop: "10px" }}>
                Include a reproduction trace, potential impact assessment, and
                affected endpoints or source files. We commit to acknowledging
                reports within 48 hours.
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
                03. SAFE HARBOR GUIDELINES
              </h2>
              <p>
                We consider research conducted in good faith under this policy to
                be authorized. When investigating vulnerabilities:
              </p>
              <ul style={{ paddingLeft: "20px", marginTop: "6px" }}>
                <li>Do not access or attempt to access private applicant data.</li>
                <li>Do not execute denial-of-service or volumetric spam attacks.</li>
                <li>Do not extort or publicly disclose issues before coordinated remediation.</li>
                <li>Give the maintainer reasonable time to deploy a patch before public disclosure.</li>
              </ul>
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
            <span>DOCUMENT // HL-SEC-001</span>
            <span>RFC 9116 COMPLIANT</span>
            <span>HELIOS SYSTEMS</span>
          </div>
        </div>
      </div>
    </div>
  );
}
