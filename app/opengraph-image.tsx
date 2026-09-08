import { ImageResponse } from "next/og";

export const runtime = "edge";
export const alt = "HELIOS — The Control Plane for AI Agents";
export const size = {
  width: 1200,
  height: 630,
};
export const contentType = "image/png";

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          backgroundColor: "#0A0A08",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "48px",
        }}
      >
        <div
          style={{
            width: "100%",
            height: "100%",
            backgroundColor: "#DED8CB",
            border: "2px solid #171714",
            padding: "48px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            fontFamily: "monospace",
            color: "#171714",
          }}
        >
          {/* Header strip */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderBottom: "1px solid #171714",
              paddingBottom: "16px",
              fontSize: "14px",
              letterSpacing: "4px",
              color: "#625F56",
            }}
          >
            <div>HX-001 // PROPERTY OF HELIOS</div>
            <div>FIELD UNIT</div>
            <div>AI SYSTEMS IN CONTEXT</div>
          </div>

          {/* Central Plate */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
            }}
          >
            <div
              style={{
                fontSize: "72px",
                fontWeight: 900,
                letterSpacing: "24px",
                color: "#171714",
                marginBottom: "12px",
              }}
            >
              H E L I O S
            </div>
            <div
              style={{
                fontSize: "20px",
                fontWeight: 700,
                letterSpacing: "4px",
                color: "#171714",
                marginBottom: "12px",
              }}
            >
              THE CONTROL PLANE FOR AI AGENTS
            </div>
            <div
              style={{
                fontSize: "14px",
                fontWeight: 700,
                letterSpacing: "5px",
                color: "#4C8F78",
              }}
            >
              OBSERVE / CONSTRAIN / ENABLE
            </div>
          </div>

          {/* Footer strip */}
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              borderTop: "1px solid #171714",
              paddingTop: "16px",
              fontSize: "13px",
              letterSpacing: "3px",
              color: "#625F56",
            }}
          >
            <div>REF HL-GOV-001</div>
            <div>INTAKE CHANNEL: ACCEPTING BETA REQUESTS</div>
            <div>SERIAL 23-5931-7A</div>
          </div>
        </div>
      </div>
    ),
    {
      ...size,
    }
  );
}
