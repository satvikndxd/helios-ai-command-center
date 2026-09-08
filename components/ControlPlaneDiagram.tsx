import React from "react";

export default function ControlPlaneDiagram() {
  return (
    <div
      style={{
        border: "1px solid var(--color-border-paper-strong)",
        backgroundColor: "#EFECE4",
        padding: "24px 16px",
        fontFamily: "var(--font-mono)",
        margin: "24px 0",
        position: "relative",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          fontSize: "10px",
          letterSpacing: "0.2em",
          color: "var(--color-muted-ink)",
          borderBottom: "1px solid var(--color-border-paper)",
          paddingBottom: "8px",
          marginBottom: "24px",
        }}
      >
        <span>SCHEMATIC // HL-ARCH-001</span>
        <span>CONTROL PLANE ENFORCEMENT TOPOLOGY</span>
        <span>REV 1.5.0</span>
      </div>

      {/* Main Flow Container */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          maxWidth: "760px",
          margin: "0 auto",
        }}
      >
        {/* Tier 1: AI Systems */}
        <div
          style={{
            border: "1px solid #171714",
            backgroundColor: "#FFFFFF",
            padding: "12px 24px",
            textAlign: "center",
            width: "100%",
            maxWidth: "420px",
          }}
        >
          <div
            style={{
              fontSize: "10px",
              letterSpacing: "0.2em",
              color: "var(--color-muted-ink)",
              marginBottom: "4px",
            }}
          >
            UPSTREAM INITIATOR
          </div>
          <div
            style={{
              fontSize: "14px",
              fontWeight: 700,
              letterSpacing: "0.15em",
              color: "var(--color-ink)",
            }}
          >
            AUTONOMOUS AI AGENTS &amp; SYSTEMS
          </div>
          <div
            style={{
              fontSize: "10px",
              color: "var(--color-muted-ink)",
              marginTop: "4px",
              letterSpacing: "0.05em",
            }}
          >
            LangGraph • MCP Clients • Coding Agents • Autonomous Workflows
          </div>
        </div>

        {/* Directional conduit 1 */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            height: "40px",
            justifyContent: "center",
          }}
        >
          <div
            style={{
              width: "1px",
              height: "28px",
              backgroundColor: "var(--color-ink)",
            }}
          />
          <div
            style={{
              fontSize: "10px",
              lineHeight: 1,
              color: "var(--color-ink)",
              transform: "translateY(-3px)",
            }}
          >
            ▼
          </div>
        </div>

        {/* Tier 2: The HELIOS Control Plane */}
        <div
          style={{
            border: "2px solid var(--color-ink)",
            backgroundColor: "#F7F4EC",
            padding: "20px",
            width: "100%",
            maxWidth: "520px",
            boxShadow: "4px 4px 0px rgba(23, 23, 20, 0.15)",
            position: "relative",
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid #171714",
              paddingBottom: "10px",
              marginBottom: "14px",
            }}
          >
            <div>
              <span className="indicator-pip" />
              <strong
                style={{
                  fontSize: "15px",
                  letterSpacing: "0.25em",
                  color: "var(--color-ink)",
                }}
              >
                H E L I O S
              </strong>
            </div>
            <div
              style={{
                fontSize: "10px",
                letterSpacing: "0.15em",
                color: "var(--color-signal-green)",
                fontWeight: 700,
              }}
            >
              ACTIVE GOVERNANCE GATEWAY
            </div>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: "10px",
            }}
          >
            {/* Plane 01: Identity */}
            <div
              style={{
                border: "1px solid var(--color-border-paper-strong)",
                backgroundColor: "#FFFFFF",
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  fontSize: "9px",
                  letterSpacing: "0.15em",
                  color: "var(--color-muted-ink)",
                }}
              >
                PLANE // 01
              </div>
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  color: "var(--color-ink)",
                  marginTop: "2px",
                }}
              >
                IDENTITY
              </div>
              <div
                style={{
                  fontSize: "10px",
                  color: "var(--color-muted-ink)",
                  marginTop: "4px",
                }}
              >
                Agent attestation, credentials, cryptographic provenance
              </div>
            </div>

            {/* Plane 02: Policy */}
            <div
              style={{
                border: "1px solid var(--color-border-paper-strong)",
                backgroundColor: "#FFFFFF",
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  fontSize: "9px",
                  letterSpacing: "0.15em",
                  color: "var(--color-muted-ink)",
                }}
              >
                PLANE // 02
              </div>
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  color: "var(--color-ink)",
                  marginTop: "2px",
                }}
              >
                POLICY
              </div>
              <div
                style={{
                  fontSize: "10px",
                  color: "var(--color-muted-ink)",
                  marginTop: "4px",
                }}
              >
                Deterministic boundaries, tool permissions, rate controls
              </div>
            </div>

            {/* Plane 03: Evidence */}
            <div
              style={{
                border: "1px solid var(--color-border-paper-strong)",
                backgroundColor: "#FFFFFF",
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  fontSize: "9px",
                  letterSpacing: "0.15em",
                  color: "var(--color-muted-ink)",
                }}
              >
                PLANE // 03
              </div>
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  color: "var(--color-ink)",
                  marginTop: "2px",
                }}
              >
                EVIDENCE
              </div>
              <div
                style={{
                  fontSize: "10px",
                  color: "var(--color-muted-ink)",
                  marginTop: "4px",
                }}
              >
                Tamper-evident logs, execution traces, decision receipts
              </div>
            </div>

            {/* Plane 04: Assurance */}
            <div
              style={{
                border: "1px solid var(--color-border-paper-strong)",
                backgroundColor: "#FFFFFF",
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  fontSize: "9px",
                  letterSpacing: "0.15em",
                  color: "var(--color-muted-ink)",
                }}
              >
                PLANE // 04
              </div>
              <div
                style={{
                  fontSize: "12px",
                  fontWeight: 700,
                  letterSpacing: "0.1em",
                  color: "var(--color-ink)",
                  marginTop: "2px",
                }}
              >
                ASSURANCE
              </div>
              <div
                style={{
                  fontSize: "10px",
                  color: "var(--color-muted-ink)",
                  marginTop: "4px",
                }}
              >
                Continuous evaluation, drift detection, verifiable audit
              </div>
            </div>
          </div>
        </div>

        {/* Directional conduit 2 (Split) */}
        <div
          style={{
            position: "relative",
            width: "100%",
            maxWidth: "640px",
            height: "44px",
          }}
        >
          {/* Vertical stem down from HELIOS */}
          <div
            style={{
              position: "absolute",
              top: 0,
              left: "50%",
              width: "1px",
              height: "22px",
              backgroundColor: "var(--color-ink)",
              transform: "translateX(-50%)",
            }}
          />
          {/* Horizontal crossbar */}
          <div
            style={{
              position: "absolute",
              top: "22px",
              left: "16%",
              right: "16%",
              height: "1px",
              backgroundColor: "var(--color-ink)",
            }}
          />
          {/* Vertical drops */}
          <div
            style={{
              position: "absolute",
              top: "22px",
              left: "16%",
              width: "1px",
              height: "16px",
              backgroundColor: "var(--color-ink)",
            }}
          />
          <div
            style={{
              position: "absolute",
              top: "22px",
              left: "50%",
              width: "1px",
              height: "16px",
              backgroundColor: "var(--color-ink)",
              transform: "translateX(-50%)",
            }}
          />
          <div
            style={{
              position: "absolute",
              top: "22px",
              right: "16%",
              width: "1px",
              height: "16px",
              backgroundColor: "var(--color-ink)",
            }}
          />
          {/* Downward chevrons */}
          <div
            style={{
              position: "absolute",
              top: "33px",
              left: "16%",
              fontSize: "10px",
              transform: "translateX(-45%)",
              color: "var(--color-ink)",
            }}
          >
            ▼
          </div>
          <div
            style={{
              position: "absolute",
              top: "33px",
              left: "50%",
              fontSize: "10px",
              transform: "translateX(-50%)",
              color: "var(--color-ink)",
            }}
          >
            ▼
          </div>
          <div
            style={{
              position: "absolute",
              top: "33px",
              right: "16%",
              fontSize: "10px",
              transform: "translateX(45%)",
              color: "var(--color-ink)",
            }}
          >
            ▼
          </div>
        </div>

        {/* Tier 3: Guarded Targets (Models, Data, Tools) */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
            gap: "12px",
            width: "100%",
            maxWidth: "680px",
            marginTop: "4px",
          }}
        >
          {/* Models */}
          <div
            style={{
              border: "1px solid var(--color-ink)",
              backgroundColor: "#FFFFFF",
              padding: "12px",
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontSize: "9px",
                letterSpacing: "0.15em",
                color: "var(--color-muted-ink)",
              }}
            >
              EXECUTION TARGET
            </div>
            <div
              style={{
                fontSize: "12px",
                fontWeight: 700,
                letterSpacing: "0.15em",
                color: "var(--color-ink)",
                margin: "4px 0",
              }}
            >
              MODELS
            </div>
            <div
              style={{
                fontSize: "10px",
                color: "var(--color-muted-ink)",
              }}
            >
              LLM Endpoints • Gateway Routers • Local Weights
            </div>
          </div>

          {/* Data */}
          <div
            style={{
              border: "1px solid var(--color-ink)",
              backgroundColor: "#FFFFFF",
              padding: "12px",
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontSize: "9px",
                letterSpacing: "0.15em",
                color: "var(--color-muted-ink)",
              }}
            >
              EXECUTION TARGET
            </div>
            <div
              style={{
                fontSize: "12px",
                fontWeight: 700,
                letterSpacing: "0.15em",
                color: "var(--color-ink)",
                margin: "4px 0",
              }}
            >
              DATA &amp; STATE
            </div>
            <div
              style={{
                fontSize: "10px",
                color: "var(--color-muted-ink)",
              }}
            >
              Vector DBs • S3 Buckets • Git Repositories
            </div>
          </div>

          {/* Tools */}
          <div
            style={{
              border: "1px solid var(--color-ink)",
              backgroundColor: "#FFFFFF",
              padding: "12px",
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontSize: "9px",
                letterSpacing: "0.15em",
                color: "var(--color-muted-ink)",
              }}
            >
              EXECUTION TARGET
            </div>
            <div
              style={{
                fontSize: "12px",
                fontWeight: 700,
                letterSpacing: "0.15em",
                color: "var(--color-ink)",
                margin: "4px 0",
              }}
            >
              TOOLS &amp; APIS
            </div>
            <div
              style={{
                fontSize: "10px",
                color: "var(--color-muted-ink)",
              }}
            >
              MCP Servers • Cloud APIs • Shell / File I/O
            </div>
          </div>
        </div>
      </div>

      <div
        style={{
          borderTop: "1px solid var(--color-border-paper)",
          marginTop: "24px",
          paddingTop: "8px",
          display: "flex",
          justifyContent: "space-between",
          fontSize: "9px",
          color: "var(--color-muted-ink)",
          letterSpacing: "0.1em",
        }}
      >
        <span>STATUS: DETERMINISTIC POLICY ENFORCEMENT</span>
        <span>LATENCY OVERHEAD: &lt; 2.4MS</span>
      </div>
    </div>
  );
}
