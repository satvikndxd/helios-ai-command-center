"use client";

import React, { useState } from "react";
import Link from "next/link";

interface WaitlistFormProps {
  source?: string;
  variant?: "paper" | "dark";
}

interface SuccessState {
  requestId: string;
  status: string;
  channel: string;
  industry: string;
  usecase: string;
  message: string;
}

interface ErrorState {
  status: number | string;
  detail: string;
  requestId?: string;
}

const INDUSTRIES = [
  "AI Agent Systems & Autonomous Workflows",
  "Developer Tools & Infrastructure",
  "Cybersecurity & Runtime Governance",
  "Cloud Infrastructure & DevOps",
  "Enterprise Software & SaaS",
  "Fintech & Quantitative Finance",
  "Research & Frontier Model Labs",
  "Other",
];

export default function WaitlistForm({
  source = "HERO_PLATE",
  variant = "paper",
}: WaitlistFormProps) {
  const [email, setEmail] = useState("");
  const [industry, setIndustry] = useState(INDUSTRIES[0]);
  const [usecase, setUsecase] = useState("");
  const [honeypot, setHoneypot] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [success, setSuccess] = useState<SuccessState | null>(null);
  const [error, setError] = useState<ErrorState | null>(null);

  const isDark = variant === "dark";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || isSubmitting) return;

    setIsSubmitting(true);
    setError(null);

    try {
      let data;
      try {
        const res = await fetch("/api/waitlist", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email.trim(),
            industry,
            usecase: usecase.trim() || "Autonomous Agent Governance",
            hp_auth_token: honeypot,
            source,
          }),
        });
        data = await res.json();
      } catch {
        // Direct FormSubmit fallback for edge/static hosting resilience
        const fsRes = await fetch("https://formsubmit.co/ajax/satvikndxd@gmail.com", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({
            "Operator Email": email.trim(),
            "Industry / Domain": industry,
            "Agent Use Case": usecase.trim() || "Autonomous Agent Governance",
            "_subject": `[HELIOS INTAKE] Beta Access Request — ${industry}`,
            "_template": "table",
            "_captcha": "false",
          }),
        });
        const fsData = await fsRes.json().catch(() => ({}));
        data = {
          success: true,
          requestId: `HX-${Math.random().toString(36).substring(2, 6).toUpperCase()}`,
          status: "LOGGED",
          channel: "HELIOS INTAKE (DIRECT FORMSUBMIT)",
          message:
            typeof fsData.message === "string"
              ? fsData.message
              : "Your request has been recorded. Await further instruction.",
        };
      }

      if (!data || !data.success) {
        setError({
          status: data?.status || 500,
          detail: data?.detail || "INTAKE CHANNEL REJECTED REQUEST",
          requestId: data?.requestId,
        });
      } else {
        setSuccess({
          requestId: data.requestId || "HX-PENDING",
          status: data.status || "LOGGED",
          channel: data.channel || "HELIOS INTAKE (FREE FORMSUBMIT)",
          industry,
          usecase: usecase.trim() || "Autonomous Agent Governance",
          message:
            data.message ||
            "Your request has been recorded. Await further instruction.",
        });
      }
    } catch {
      setError({
        status: 503,
        detail: "NETWORK CONNECTION OR INTAKE TIMEOUT",
      });
    } finally {
      setIsSubmitting(false);
    }
  }

  if (success) {
    return (
      <div
        style={{
          border: isDark ? "1px solid #4C8F78" : "1px solid #171714",
          backgroundColor: isDark ? "#0E1410" : "#F4EFE6",
          padding: "24px",
          fontFamily: "var(--font-mono)",
        }}
        role="region"
        aria-live="polite"
        aria-label="Access Request Confirmation"
      >
        <div
          style={{
            fontSize: "11px",
            letterSpacing: "0.2em",
            color: isDark ? "#4C8F78" : "#171714",
            fontWeight: 700,
            textTransform: "uppercase",
            marginBottom: "16px",
          }}
        >
          <span className="indicator-pip" />
          ACCESS REQUEST RECEIVED // LOGGED
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "140px 1fr",
            gap: "8px",
            fontSize: "12px",
            borderBottom: isDark ? "1px solid #232320" : "1px solid #C7BFB0",
            paddingBottom: "14px",
            marginBottom: "14px",
          }}
        >
          <span style={{ color: "var(--color-muted-ink)" }}>REQUEST ID</span>
          <span style={{ fontWeight: 700, letterSpacing: "0.1em" }}>
            {success.requestId}
          </span>

          <span style={{ color: "var(--color-muted-ink)" }}>STATUS</span>
          <span style={{ color: "var(--color-signal-green)", fontWeight: 700 }}>
            {success.status}
          </span>

          <span style={{ color: "var(--color-muted-ink)" }}>INDUSTRY</span>
          <span>{success.industry}</span>

          <span style={{ color: "var(--color-muted-ink)" }}>USE CASE</span>
          <span>{success.usecase}</span>

          <span style={{ color: "var(--color-muted-ink)" }}>DISPATCH</span>
          <span>FORWARDED TO SATVIKNDXD@GMAIL.COM</span>
        </div>

        <p
          style={{
            fontSize: "13px",
            lineHeight: 1.5,
            color: isDark ? "var(--color-light-paper)" : "var(--color-ink)",
            marginBottom: "12px",
          }}
        >
          {success.message}
        </p>

        <div
          style={{
            fontSize: "11px",
            color: "var(--color-signal-green)",
            letterSpacing: "0.15em",
            fontWeight: 600,
          }}
        >
          ● REQUEST QUEUED
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        border: isDark
          ? "1px solid var(--color-border-dark)"
          : "1px solid var(--color-border-paper-strong)",
        backgroundColor: isDark ? "#0A0A08" : "#EFE9DD",
        padding: "24px 20px",
        fontFamily: "var(--font-mono)",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "11px",
          letterSpacing: "0.18em",
          color: "var(--color-muted-ink)",
          marginBottom: "16px",
          borderBottom: isDark
            ? "1px solid #1C1C18"
            : "1px solid var(--color-border-paper)",
          paddingBottom: "8px",
        }}
      >
        <span>ACCESS REQUEST // OPERATOR INTAKE</span>
        <span style={{ color: "var(--color-signal-green)" }}>
          <span className="indicator-pip" />
          ACCEPTING
        </span>
      </div>

      <form onSubmit={handleSubmit} noValidate>
        {/* Honeypot field */}
        <div
          style={{ position: "absolute", left: "-9999px", opacity: 0 }}
          aria-hidden="true"
        >
          <label htmlFor={`hp_token_${source}`}>Security Token</label>
          <input
            id={`hp_token_${source}`}
            type="text"
            name="hp_auth_token"
            tabIndex={-1}
            autoComplete="off"
            value={honeypot}
            onChange={(e) => setHoneypot(e.target.value)}
          />
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
            gap: "14px",
            marginBottom: "14px",
          }}
        >
          {/* Email field */}
          <div>
            <label
              htmlFor={`email_input_${source}`}
              style={{
                display: "block",
                fontSize: "11px",
                letterSpacing: "0.15em",
                color: isDark ? "var(--color-paper)" : "var(--color-ink)",
                fontWeight: 600,
                marginBottom: "6px",
              }}
            >
              01. OPERATOR EMAIL *
            </label>
            <input
              id={`email_input_${source}`}
              type="email"
              required
              placeholder="operator@company.dev"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={isSubmitting}
              className={isDark ? "terminal-input-dark" : "terminal-input"}
              aria-label="Operator email address"
            />
          </div>

          {/* Industry field */}
          <div>
            <label
              htmlFor={`industry_input_${source}`}
              style={{
                display: "block",
                fontSize: "11px",
                letterSpacing: "0.15em",
                color: isDark ? "var(--color-paper)" : "var(--color-ink)",
                fontWeight: 600,
                marginBottom: "6px",
              }}
            >
              02. INDUSTRY / DOMAIN *
            </label>
            <select
              id={`industry_input_${source}`}
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              disabled={isSubmitting}
              className={isDark ? "terminal-input-dark" : "terminal-input"}
              style={{ cursor: "pointer", appearance: "auto" }}
              aria-label="Industry sector"
            >
              {INDUSTRIES.map((ind) => (
                <option key={ind} value={ind}>
                  {ind}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Usecase field */}
        <div style={{ marginBottom: "16px" }}>
          <label
            htmlFor={`usecase_input_${source}`}
            style={{
              display: "block",
              fontSize: "11px",
              letterSpacing: "0.15em",
              color: isDark ? "var(--color-paper)" : "var(--color-ink)",
              fontWeight: 600,
              marginBottom: "6px",
            }}
          >
            03. AGENT USE CASE / ARCHITECTURE *
          </label>
          <input
            id={`usecase_input_${source}`}
            type="text"
            required
            placeholder="e.g. LangGraph agents calling GitHub APIs & internal SQL databases"
            value={usecase}
            onChange={(e) => setUsecase(e.target.value)}
            disabled={isSubmitting}
            className={isDark ? "terminal-input-dark" : "terminal-input"}
            aria-label="Agent use case description"
          />
        </div>

        {/* Submit button */}
        <div style={{ display: "flex", justifyContent: "flex-start" }}>
          <button
            type="submit"
            disabled={isSubmitting || !email.trim()}
            className={
              isDark ? "terminal-btn terminal-btn-green" : "terminal-btn"
            }
            style={{ width: "100%", padding: "14px 24px" }}
          >
            {isSubmitting
              ? "[ DISPATCHING VIA INTAKE... ]"
              : "REQUEST BETA ACCESS →"}
          </button>
        </div>

        {error && (
          <div
            style={{
              backgroundColor: isDark ? "#1C1210" : "#F8E5E2",
              border: "1px solid #B33D3D",
              color: isDark ? "#F88" : "#8A1F1F",
              padding: "10px 14px",
              fontSize: "11px",
              letterSpacing: "0.08em",
              marginTop: "14px",
            }}
            role="alert"
          >
            <div style={{ fontWeight: 700, marginBottom: "4px" }}>
              REQUEST FAILED // STATUS {error.status}
            </div>
            <div>{error.detail}</div>
            {error.requestId && (
              <div style={{ fontSize: "10px", marginTop: "4px", opacity: 0.8 }}>
                DIAGNOSTIC ID: {error.requestId}
              </div>
            )}
          </div>
        )}

        <div
          style={{
            fontSize: "11px",
            lineHeight: 1.5,
            color: "var(--color-muted-ink)",
            marginTop: "12px",
          }}
        >
          Your details are used solely to process your beta access request and
          delivered to the operator. By submitting, you agree to the{" "}
          <Link
            href="/terms"
            style={{
              color: isDark ? "var(--color-paper)" : "var(--color-ink)",
              textDecoration: "underline",
            }}
          >
            Terms of Use
          </Link>{" "}
          and acknowledge the{" "}
          <Link
            href="/privacy"
            style={{
              color: isDark ? "var(--color-paper)" : "var(--color-ink)",
              textDecoration: "underline",
            }}
          >
            Privacy Directive
          </Link>
          . Powered by free FormSubmit.
        </div>
      </form>
    </div>
  );
}
