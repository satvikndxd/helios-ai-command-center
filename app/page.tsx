import React from "react";
import Image from "next/image";
import Link from "next/link";
import WaitlistForm from "@/components/WaitlistForm";
import ControlPlaneDiagram from "@/components/ControlPlaneDiagram";

export default function HeliosLandingPage() {
  return (
    <div style={{ minHeight: "100vh", backgroundColor: "var(--color-near-black)" }}>
      {/* Top Technical Telemetry Strip */}
      <header
        style={{
          borderBottom: "1px solid var(--color-border-dark)",
          backgroundColor: "#070706",
          padding: "10px 20px",
          position: "sticky",
          top: 0,
          zIndex: 100,
          backdropFilter: "blur(8px)",
        }}
      >
        <div
          style={{
            maxWidth: "1180px",
            margin: "0 auto",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
            gap: "12px",
            fontSize: "11px",
            letterSpacing: "0.15em",
            fontFamily: "var(--font-mono)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            <span style={{ color: "var(--color-paper)", fontWeight: 700 }}>
              HELIOS // HX-001
            </span>
            <span style={{ color: "var(--color-muted-ink)" }}>
              FIELD UNIT [PROD]
            </span>
          </div>

          <nav
            style={{
              display: "flex",
              gap: "18px",
              flexWrap: "wrap",
              color: "var(--color-muted-ink)",
            }}
            aria-label="Dossier section navigation"
          >
            <a href="#sheet-001" style={{ color: "inherit", textDecoration: "none" }}>
              01 IDENTIFICATION
            </a>
            <a href="#sheet-002" style={{ color: "inherit", textDecoration: "none" }}>
              02 THE PROBLEM
            </a>
            <a href="#sheet-003" style={{ color: "inherit", textDecoration: "none" }}>
              03 FOUR QUESTIONS
            </a>
            <a href="#sheet-004" style={{ color: "inherit", textDecoration: "none" }}>
              04 CONTROL PLANE
            </a>
            <a href="#sheet-005" style={{ color: "inherit", textDecoration: "none" }}>
              05 TENETS
            </a>
            <a href="#sheet-006" style={{ color: "var(--color-paper)", textDecoration: "none" }}>
              06 ACCESS REQUEST →
            </a>
          </nav>

          <div style={{ display: "flex", alignItems: "center" }}>
            <span className="indicator-pip" />
            <span style={{ color: "var(--color-signal-green)" }}>
              INTAKE ACTIVE
            </span>
          </div>
        </div>
      </header>

      {/* Main Dossier Stream */}
      <main style={{ maxWidth: "1180px", margin: "0 auto", padding: "32px 16px 80px" }}>
        
        {/* ================================================================= */}
        {/* SHEET 001 // IDENTIFICATION & EQUIPMENT PLATE HERO                */}
        {/* ================================================================= */}
        <section
          id="sheet-001"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 001: Helios Identification"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 001 // IDENTIFICATION</span>
            <span>CLASSIFIED FIELD SPECIFICATION</span>
            <span>RESTRICTED DISTRIBUTION</span>
          </div>

          {/* Physical Equipment Plate Hero */}
          <div
            className="dossier-sheet"
            style={{
              padding: "36px 32px",
              border: "1px solid var(--color-border-paper-strong)",
              position: "relative",
            }}
          >
            {/* Header Plate Metadata */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto 1fr",
                alignItems: "center",
                borderBottom: "1px solid var(--color-border-paper-strong)",
                paddingBottom: "16px",
                marginBottom: "32px",
                fontFamily: "var(--font-mono)",
                fontSize: "11px",
                letterSpacing: "0.18em",
                color: "var(--color-muted-ink)",
              }}
            >
              <div>
                <div>HX-001</div>
                <div>FIELD UNIT</div>
              </div>
              <div style={{ textAlign: "center", fontWeight: 600, color: "var(--color-ink)" }}>
                PROPERTY OF HELIOS<br />
                CONTROLLED SYSTEM
              </div>
              <div style={{ textAlign: "right" }}>
                <div>AI SYSTEMS IN CONTEXT</div>
                <div>UNDER CONTROL</div>
              </div>
            </div>

            {/* Core Plate Grid: Logo + Insignia + Headline */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: "40px",
                alignItems: "center",
                marginBottom: "36px",
              }}
            >
              {/* Left Column: Stamped Wordmark & Core Copy */}
              <div>
                <div
                  style={{
                    fontSize: "11px",
                    letterSpacing: "0.25em",
                    color: "var(--color-muted-ink)",
                    marginBottom: "8px",
                    fontFamily: "var(--font-mono)",
                  }}
                >
                  SYSTEM CODENAME // HL-GOV-001
                </div>

                <h1
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "clamp(42px, 6vw, 68px)",
                    fontWeight: 800,
                    letterSpacing: "0.35em",
                    color: "var(--color-ink)",
                    lineHeight: 1.1,
                    textTransform: "uppercase",
                    margin: "0 0 16px 0",
                  }}
                >
                  H E L I O S
                </h1>

                <div
                  style={{
                    fontSize: "15px",
                    fontWeight: 700,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    fontFamily: "var(--font-mono)",
                    textTransform: "uppercase",
                    marginBottom: "8px",
                  }}
                >
                  THE CONTROL PLANE FOR AI AGENTS
                </div>

                <div
                  style={{
                    fontSize: "12px",
                    letterSpacing: "0.2em",
                    color: "var(--color-signal-green)",
                    fontWeight: 700,
                    fontFamily: "var(--font-mono)",
                    marginBottom: "20px",
                  }}
                >
                  OBSERVE / CONSTRAIN / ENABLE
                </div>

                <p
                  style={{
                    fontSize: "15px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                    fontFamily: "var(--font-mono)",
                    maxWidth: "540px",
                    marginBottom: "16px",
                  }}
                >
                  AI systems are becoming capable of taking actions. HELIOS gives those
                  actions <strong>identity</strong>, <strong>policy</strong>,{" "}
                  <strong>evidence</strong>, and <strong>control</strong>.
                </p>

                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-muted-ink)",
                    fontFamily: "var(--font-mono)",
                    maxWidth: "540px",
                  }}
                >
                  Define what AI agents are allowed to do. Control their runtime
                  actions. Prove what actually happened.
                </p>
              </div>

              {/* Right Column: Original Equipment Plate Insignia Asset */}
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <div
                  style={{
                    border: "1px solid var(--color-ink)",
                    backgroundColor: "#0A0A08",
                    padding: "8px",
                    boxShadow: "6px 6px 0px rgba(23, 23, 20, 0.2)",
                    maxWidth: "360px",
                    width: "100%",
                  }}
                >
                  <Image
                    src="/assets/helios-logo.png"
                    alt="HELIOS Equipment Plate — Governed AI Command Center"
                    width={400}
                    height={400}
                    priority
                    style={{
                      width: "100%",
                      height: "auto",
                      display: "block",
                      border: "1px solid #232320",
                    }}
                  />
                  <div
                    style={{
                      padding: "8px 4px 4px",
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "9px",
                      letterSpacing: "0.15em",
                      color: "var(--color-muted-ink)",
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    <span>REF HL-GOV-001</span>
                    <span>SERIAL 23-5931-7A</span>
                    <span>DO NOT REMOVE</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Terminal Access Intake Module (Hero CTA) */}
            <div style={{ marginTop: "32px" }}>
              <WaitlistForm source="HERO_EQUIPMENT_PLATE" variant="paper" />
            </div>

            {/* Plate Footer Metadata */}
            <div
              style={{
                marginTop: "24px",
                borderTop: "1px solid var(--color-border-paper)",
                paddingTop: "12px",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: "8px",
                fontSize: "10px",
                letterSpacing: "0.15em",
                color: "var(--color-muted-ink)",
                fontFamily: "var(--font-mono)",
              }}
            >
              <span>CLASS // RUNTIME GOVERNANCE</span>
              <span>AUTH MODEL // LEAST PRIVILEGE</span>
              <span>ENV // PRODUCTION GATEWAY</span>
              <span>REV // 1.5.0</span>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SHEET 002 // THE PROBLEM                                          */}
        {/* ================================================================= */}
        <section
          id="sheet-002"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 002: The Problem"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 002 // THE PROBLEM</span>
            <span>SYSTEM AUDIT &amp; THREAT VECTOR</span>
            <span>UNGOVERNED AUTONOMY</span>
          </div>

          <div
            className="dossier-sheet"
            style={{
              padding: "36px 32px",
              backgroundColor: "var(--color-light-paper)",
            }}
          >
            <div
              style={{
                fontSize: "11px",
                letterSpacing: "0.2em",
                color: "var(--color-muted-ink)",
                marginBottom: "8px",
                fontFamily: "var(--font-mono)",
              }}
            >
              CORE THESIS
            </div>

            <h2
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "clamp(22px, 3.5vw, 34px)",
                fontWeight: 800,
                letterSpacing: "0.08em",
                color: "var(--color-ink)",
                lineHeight: 1.3,
                textTransform: "uppercase",
                marginBottom: "24px",
                maxWidth: "920px",
              }}
            >
              AI AGENTS ARE ACQUIRING PERMISSIONS FASTER THAN ORGANIZATIONS ARE
              ACQUIRING CONTROL.
            </h2>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: "28px",
                fontFamily: "var(--font-mono)",
                marginBottom: "32px",
              }}
            >
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                }}
              >
                <div
                  style={{
                    fontSize: "11px",
                    fontWeight: 700,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    marginBottom: "12px",
                  }}
                >
                  WHAT AGENTS CAN DO TODAY
                </div>
                <ul
                  style={{
                    listStyle: "none",
                    padding: 0,
                    margin: 0,
                    fontSize: "13px",
                    lineHeight: 1.8,
                    color: "var(--color-ink)",
                  }}
                >
                  <li>[✓] Read proprietary codebases &amp; data stores</li>
                  <li>[✓] Call arbitrary internal &amp; external APIs</li>
                  <li>[✓] Modify git repositories &amp; open PRs</li>
                  <li>[✓] Execute shell commands &amp; spawn workers</li>
                  <li>[✓] Provision cloud infrastructure &amp; buckets</li>
                  <li>[✓] Execute multi-step autonomous workflows</li>
                </ul>
              </div>

              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                }}
              >
                <div
                  style={{
                    fontSize: "11px",
                    fontWeight: 700,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    marginBottom: "12px",
                  }}
                >
                  THE GOVERNANCE VOID
                </div>
                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                    marginBottom: "12px",
                  }}
                >
                  The fundamental question for autonomous software isn&apos;t simply:
                </p>
                <blockquote
                  style={{
                    borderLeft: "2px solid var(--color-muted-ink)",
                    paddingLeft: "12px",
                    fontStyle: "italic",
                    color: "var(--color-muted-ink)",
                    marginBottom: "12px",
                  }}
                >
                  &ldquo;Is the model intelligent?&rdquo;
                </blockquote>
                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                  }}
                >
                  The real operational question is:
                  <strong style={{ display: "block", marginTop: "6px" }}>
                    &ldquo;Who is this AI system, what is it allowed to do, what
                    did it actually do, and can we prove it stayed within
                    policy?&rdquo;
                  </strong>
                </p>
              </div>
            </div>

            {/* Core Message Callout */}
            <div
              style={{
                border: "2px solid var(--color-ink)",
                backgroundColor: "#F7F4EC",
                padding: "24px",
                position: "relative",
              }}
            >
              <div
                style={{
                  fontSize: "10px",
                  letterSpacing: "0.2em",
                  color: "var(--color-signal-green)",
                  fontWeight: 700,
                  marginBottom: "8px",
                  fontFamily: "var(--font-mono)",
                }}
              >
                <span className="indicator-pip" />
                FOUNDATIONAL PRINCIPLE // SEC 38
              </div>
              <p
                style={{
                  fontSize: "16px",
                  lineHeight: 1.6,
                  color: "var(--color-ink)",
                  fontWeight: 600,
                  fontFamily: "var(--font-mono)",
                  margin: 0,
                }}
              >
                The problem with autonomous AI isn&apos;t only what a model can
                generate. It&apos;s what the system can actually do. HELIOS gives
                those actions an identity, a policy boundary, and an evidence
                trail.
              </p>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SHEET 003 // THE FOUR QUESTIONS (THE PLANES)                      */}
        {/* ================================================================= */}
        <section
          id="sheet-003"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 003: The Four Fundamental Questions"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 003 // FOUR QUESTIONS</span>
            <span>SYSTEM SPECIFICATION</span>
            <span>THE PLANES OF GOVERNANCE</span>
          </div>

          <div
            className="dossier-sheet"
            style={{
              padding: "36px 32px",
            }}
          >
            <div
              style={{
                borderBottom: "1px solid var(--color-border-paper-strong)",
                paddingBottom: "16px",
                marginBottom: "28px",
              }}
            >
              <div
                style={{
                  fontSize: "11px",
                  letterSpacing: "0.2em",
                  color: "var(--color-muted-ink)",
                  fontFamily: "var(--font-mono)",
                  marginBottom: "6px",
                }}
              >
                ARCHITECTURAL DECONSTRUCTION
              </div>
              <h2
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "24px",
                  fontWeight: 800,
                  letterSpacing: "0.15em",
                  color: "var(--color-ink)",
                  textTransform: "uppercase",
                }}
              >
                FOUR FUNDAMENTAL INQUIRIES
              </h2>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                gap: "20px",
                fontFamily: "var(--font-mono)",
              }}
            >
              {/* Question 01 */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "10px",
                      letterSpacing: "0.15em",
                      color: "var(--color-muted-ink)",
                      marginBottom: "8px",
                    }}
                  >
                    <span>ITEM // 01</span>
                    <span style={{ color: "var(--color-signal-green)", fontWeight: 700 }}>
                      PLANE: IDENTITY
                    </span>
                  </div>
                  <h3
                    style={{
                      fontSize: "16px",
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      color: "var(--color-ink)",
                      lineHeight: 1.4,
                      marginBottom: "12px",
                    }}
                  >
                    WHAT AI SYSTEMS DO WE HAVE?
                  </h3>
                  <p
                    style={{
                      fontSize: "12px",
                      lineHeight: 1.6,
                      color: "var(--color-muted-ink)",
                    }}
                  >
                    Discover, register, and attest every autonomous agent, worker,
                    LLM wrapper, and MCP server across all repositories and
                    environments.
                  </p>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--color-border-paper)",
                    paddingTop: "10px",
                    marginTop: "16px",
                    fontSize: "10px",
                    color: "var(--color-ink)",
                  }}
                >
                  SYSTEM REGISTRY • AGENT CREDENTIALS • PROVENANCE
                </div>
              </div>

              {/* Question 02 */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "10px",
                      letterSpacing: "0.15em",
                      color: "var(--color-muted-ink)",
                      marginBottom: "8px",
                    }}
                  >
                    <span>ITEM // 02</span>
                    <span style={{ color: "var(--color-signal-green)", fontWeight: 700 }}>
                      PLANE: POLICY
                    </span>
                  </div>
                  <h3
                    style={{
                      fontSize: "16px",
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      color: "var(--color-ink)",
                      lineHeight: 1.4,
                      marginBottom: "12px",
                    }}
                  >
                    WHAT ARE THEY ALLOWED TO DO?
                  </h3>
                  <p
                    style={{
                      fontSize: "12px",
                      lineHeight: 1.6,
                      color: "var(--color-muted-ink)",
                    }}
                  >
                    Define declarative, versioned policy boundaries. Control tool
                    invocations, API scopes, file access, token expenditure, and
                    human-in-the-loop triggers.
                  </p>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--color-border-paper)",
                    paddingTop: "10px",
                    marginTop: "16px",
                    fontSize: "10px",
                    color: "var(--color-ink)",
                  }}
                >
                  BOUNDARIES • LEAST PRIVILEGE • ESCALATION GATES
                </div>
              </div>

              {/* Question 03 */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "10px",
                      letterSpacing: "0.15em",
                      color: "var(--color-muted-ink)",
                      marginBottom: "8px",
                    }}
                  >
                    <span>ITEM // 03</span>
                    <span style={{ color: "var(--color-signal-green)", fontWeight: 700 }}>
                      PLANE: EVIDENCE
                    </span>
                  </div>
                  <h3
                    style={{
                      fontSize: "16px",
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      color: "var(--color-ink)",
                      lineHeight: 1.4,
                      marginBottom: "12px",
                    }}
                  >
                    WHAT DID THEY ACTUALLY DO?
                  </h3>
                  <p
                    style={{
                      fontSize: "12px",
                      lineHeight: 1.6,
                      color: "var(--color-muted-ink)",
                    }}
                  >
                    Capture immutable, tamper-evident execution receipts. Every
                    model invocation, tool parameter, state mutation, and
                    output payload is cryptographically logged.
                  </p>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--color-border-paper)",
                    paddingTop: "10px",
                    marginTop: "16px",
                    fontSize: "10px",
                    color: "var(--color-ink)",
                  }}
                >
                  DECISION RECEIPTS • TRACES • MERKLE AUDIT
                </div>
              </div>

              {/* Question 04 */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "20px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                }}
              >
                <div>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      fontSize: "10px",
                      letterSpacing: "0.15em",
                      color: "var(--color-signal-green)",
                      marginBottom: "8px",
                    }}
                  >
                    <span style={{ color: "var(--color-muted-ink)" }}>ITEM // 04</span>
                    <span style={{ fontWeight: 700 }}>PLANE: ASSURANCE</span>
                  </div>
                  <h3
                    style={{
                      fontSize: "16px",
                      fontWeight: 700,
                      letterSpacing: "0.08em",
                      color: "var(--color-ink)",
                      lineHeight: 1.4,
                      marginBottom: "12px",
                    }}
                  >
                    CAN WE PROVE THEY STAYED WITHIN POLICY?
                  </h3>
                  <p
                    style={{
                      fontSize: "12px",
                      lineHeight: 1.6,
                      color: "var(--color-muted-ink)",
                    }}
                  >
                    Continuous evaluation, behavioral drift monitoring, and
                    replayable audit verification that satisfy engineering,
                    security, and external scrutiny.
                  </p>
                </div>
                <div
                  style={{
                    borderTop: "1px solid var(--color-border-paper)",
                    paddingTop: "10px",
                    marginTop: "16px",
                    fontSize: "10px",
                    color: "var(--color-ink)",
                  }}
                >
                  EVALUATION • DRIFT DETECTION • REPLAY VERIFICATION
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SHEET 004 // THE CONTROL PLANE ARCHITECTURE                       */}
        {/* ================================================================= */}
        <section
          id="sheet-004"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 004: Control Plane Architecture"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 004 // THE CONTROL PLANE</span>
            <span>SYSTEM TOPOLOGY &amp; MEDIATION</span>
            <span>AIR-GAPPED CONTROL BOUNDARY</span>
          </div>

          <div
            className="dossier-sheet"
            style={{
              padding: "36px 32px",
            }}
          >
            <div
              style={{
                borderBottom: "1px solid var(--color-border-paper-strong)",
                paddingBottom: "16px",
                marginBottom: "24px",
              }}
            >
              <div
                style={{
                  fontSize: "11px",
                  letterSpacing: "0.2em",
                  color: "var(--color-muted-ink)",
                  fontFamily: "var(--font-mono)",
                  marginBottom: "6px",
                }}
              >
                INTERCEPTOR TOPOLOGY
              </div>
              <h2
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: "24px",
                  fontWeight: 800,
                  letterSpacing: "0.15em",
                  color: "var(--color-ink)",
                  textTransform: "uppercase",
                }}
              >
                THE ARCHITECTURAL CONTROL PLANE
              </h2>
            </div>

            <p
              style={{
                fontSize: "14px",
                lineHeight: 1.6,
                color: "var(--color-ink)",
                fontFamily: "var(--font-mono)",
                maxWidth: "780px",
                marginBottom: "20px",
              }}
            >
              HELIOS operates as a synchronous and asynchronous governance
              gateway. Autonomous agents interact through HELIOS mediation
              layers before any downstream tool, model, or database action is
              authorized.
            </p>

            {/* Visual SVG / HTML Architecture Diagram */}
            <ControlPlaneDiagram />

            {/* Specifications Strip */}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: "16px",
                marginTop: "24px",
                fontFamily: "var(--font-mono)",
              }}
            >
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "14px",
                }}
              >
                <div style={{ fontSize: "10px", color: "var(--color-muted-ink)" }}>
                  LATENCY OVERHEAD
                </div>
                <div
                  style={{
                    fontSize: "16px",
                    fontWeight: 700,
                    color: "var(--color-ink)",
                    margin: "4px 0",
                  }}
                >
                  &lt; 3ms Gate Evaluation
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-muted-ink)" }}>
                  In-process policy evaluation with zero impact on agent throughput.
                </div>
              </div>

              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "14px",
                }}
              >
                <div style={{ fontSize: "10px", color: "var(--color-muted-ink)" }}>
                  INTEGRATION INTERFACES
                </div>
                <div
                  style={{
                    fontSize: "16px",
                    fontWeight: 700,
                    color: "var(--color-ink)",
                    margin: "4px 0",
                  }}
                >
                  MCP / REST / SDK
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-muted-ink)" }}>
                  Native Model Context Protocol proxies, Python SDK, and CLI interceptors.
                </div>
              </div>

              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "14px",
                }}
              >
                <div style={{ fontSize: "10px", color: "var(--color-muted-ink)" }}>
                  ASSURANCE RUNTIME
                </div>
                <div
                  style={{
                    fontSize: "16px",
                    fontWeight: 700,
                    color: "var(--color-signal-green)",
                    margin: "4px 0",
                  }}
                >
                  Tamper-Evident Receipts
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-muted-ink)" }}>
                  Cryptographic hashing of decisions for zero-trust audits.
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SHEET 005 // OBSERVE / CONSTRAIN / ENABLE                         */}
        {/* ================================================================= */}
        <section
          id="sheet-005"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 005: Operational Tenets"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 005 // OPERATIONAL TENETS</span>
            <span>CORE DOCTRINE</span>
            <span>AUTONOMY IN EQUILIBRIUM</span>
          </div>

          <div
            className="dossier-sheet"
            style={{
              padding: "36px 32px",
            }}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                gap: "24px",
                fontFamily: "var(--font-mono)",
              }}
            >
              {/* Tenet 1: OBSERVE */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "24px",
                  position: "relative",
                }}
              >
                <div
                  style={{
                    fontSize: "10px",
                    letterSpacing: "0.2em",
                    color: "var(--color-muted-ink)",
                    marginBottom: "8px",
                  }}
                >
                  TENET // 01
                </div>
                <h3
                  style={{
                    fontSize: "20px",
                    fontWeight: 800,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    textTransform: "uppercase",
                    marginBottom: "8px",
                  }}
                >
                  OBSERVE
                </h3>
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--color-signal-green)",
                    fontWeight: 700,
                    marginBottom: "14px",
                  }}
                >
                  <span className="indicator-pip" />
                  KNOW WHAT THE SYSTEM IS DOING
                </div>
                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                  }}
                >
                  Complete continuous visibility into active agent sessions,
                  tool invocations, context size, model selection, and memory
                  mutations without invasive code rewrites.
                </p>
              </div>

              {/* Tenet 2: CONSTRAIN */}
              <div
                style={{
                  border: "2px solid var(--color-ink)",
                  backgroundColor: "#FFFFFF",
                  padding: "24px",
                  position: "relative",
                  boxShadow: "4px 4px 0px rgba(23, 23, 20, 0.1)",
                }}
              >
                <div
                  style={{
                    fontSize: "10px",
                    letterSpacing: "0.2em",
                    color: "var(--color-signal-green)",
                    fontWeight: 700,
                    marginBottom: "8px",
                  }}
                >
                  TENET // 02 [ACTIVE GATE]
                </div>
                <h3
                  style={{
                    fontSize: "20px",
                    fontWeight: 800,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    textTransform: "uppercase",
                    marginBottom: "8px",
                  }}
                >
                  CONSTRAIN
                </h3>
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--color-ink)",
                    fontWeight: 700,
                    marginBottom: "14px",
                  }}
                >
                  <span className="indicator-pip" />
                  DEFINE WHAT THE SYSTEM IS PERMITTED TO DO
                </div>
                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                  }}
                >
                  Hard guardrails and deterministic runtime gates. Enforce least
                  privilege, restrict critical tool APIs, rate-limit execution,
                  and route anomalous actions to human review.
                </p>
              </div>

              {/* Tenet 3: ENABLE */}
              <div
                style={{
                  border: "1px solid var(--color-border-paper-strong)",
                  backgroundColor: "#FFFFFF",
                  padding: "24px",
                  position: "relative",
                }}
              >
                <div
                  style={{
                    fontSize: "10px",
                    letterSpacing: "0.2em",
                    color: "var(--color-muted-ink)",
                    marginBottom: "8px",
                  }}
                >
                  TENET // 03
                </div>
                <h3
                  style={{
                    fontSize: "20px",
                    fontWeight: 800,
                    letterSpacing: "0.15em",
                    color: "var(--color-ink)",
                    textTransform: "uppercase",
                    marginBottom: "8px",
                  }}
                >
                  ENABLE
                </h3>
                <div
                  style={{
                    fontSize: "12px",
                    color: "var(--color-signal-green)",
                    fontWeight: 700,
                    marginBottom: "14px",
                  }}
                >
                  <span className="indicator-pip" />
                  ALLOW USEFUL AUTONOMY WITHOUT SURRENDERING CONTROL
                </div>
                <p
                  style={{
                    fontSize: "13px",
                    lineHeight: 1.6,
                    color: "var(--color-ink)",
                  }}
                >
                  Engineers deploy agents with confidence. Organizations grant
                  real operational permissions because every action is bounded,
                  recorded, and verifiable.
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* SHEET 006 // FINAL ACCESS REQUEST INTAKE                          */}
        {/* ================================================================= */}
        <section
          id="sheet-006"
          style={{ marginBottom: "64px" }}
          aria-label="Sheet 006: Final Access Request"
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "12px",
              fontSize: "11px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              fontFamily: "var(--font-mono)",
            }}
          >
            <span>SHEET 006 // ACCESS REQUEST</span>
            <span>OPERATOR INTAKE GATEWAY</span>
            <span>BETA ALLOCATION</span>
          </div>

          <div
            style={{
              backgroundColor: "#0A0A08",
              border: "1px solid var(--color-border-dark)",
              padding: "48px 32px",
              textAlign: "center",
            }}
          >
            <div
              style={{
                maxWidth: "680px",
                margin: "0 auto",
                fontFamily: "var(--font-mono)",
              }}
            >
              <div
                style={{
                  fontSize: "11px",
                  letterSpacing: "0.3em",
                  color: "var(--color-signal-green)",
                  marginBottom: "16px",
                  fontWeight: 700,
                }}
              >
                <span className="indicator-pip" />
                INTAKE CHANNEL ACTIVE // ACCEPTING OPERATORS
              </div>

              <h2
                style={{
                  fontSize: "clamp(32px, 5vw, 48px)",
                  fontWeight: 800,
                  letterSpacing: "0.25em",
                  color: "var(--color-paper)",
                  textTransform: "uppercase",
                  marginBottom: "12px",
                }}
              >
                H E L I O S
              </h2>

              <div
                style={{
                  fontSize: "14px",
                  letterSpacing: "0.15em",
                  color: "var(--color-muted-ink)",
                  textTransform: "uppercase",
                  marginBottom: "16px",
                }}
              >
                THE CONTROL PLANE FOR AI AGENTS
              </div>

              <p
                style={{
                  fontSize: "14px",
                  lineHeight: 1.6,
                  color: "var(--color-paper)",
                  marginBottom: "32px",
                }}
              >
                Beta access is limited to engineers building autonomous agents,
                MCP-based architectures, and multi-agent infrastructure.
              </p>

              {/* Terminal Intake Form in Dark Variant */}
              <div style={{ textAlign: "left" }}>
                <WaitlistForm source="SHEET_006_FINAL" variant="dark" />
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* =================================================================== */}
      {/* DOSSIER FOOTER                                                      */}
      {/* =================================================================== */}
      <footer
        style={{
          borderTop: "1px solid var(--color-border-dark)",
          backgroundColor: "#050504",
          padding: "40px 20px",
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
          color: "var(--color-muted-ink)",
          letterSpacing: "0.15em",
        }}
      >
        <div
          style={{
            maxWidth: "1180px",
            margin: "0 auto",
            display: "flex",
            flexDirection: "column",
            gap: "24px",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "16px",
              borderBottom: "1px solid #161613",
              paddingBottom: "16px",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <span style={{ color: "var(--color-paper)", fontWeight: 700 }}>
                PROPERTY OF HELIOS
              </span>
              <span>//</span>
              <span>GOVERNED AI CONTROL</span>
            </div>

            <div style={{ display: "flex", gap: "20px" }}>
              <Link
                href="/privacy"
                style={{ color: "var(--color-paper)", textDecoration: "none" }}
              >
                PRIVACY DIRECTIVE
              </Link>
              <Link
                href="/terms"
                style={{ color: "var(--color-paper)", textDecoration: "none" }}
              >
                TERMS OF USE
              </Link>
              <Link
                href="/security"
                style={{ color: "var(--color-paper)", textDecoration: "none" }}
              >
                SECURITY
              </Link>
            </div>
          </div>

          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: "12px",
              fontSize: "10px",
            }}
          >
            <div>
              HX-001 // FIELD UNIT // REV 1.5.0 • OBSERVE / CONSTRAIN / ENABLE
            </div>
            <div>
              AI SYSTEMS IN CONTEXT. UNDER CONTROL. © {new Date().getFullYear()} HELIOS
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
