import type { Metadata, Viewport } from "next";
import "./globals.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL || "https://helios-ai-command-center.vercel.app";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: "HELIOS — AI Agent Governance & Control",
  description:
    "HELIOS is the control plane for AI agents, providing identity, policy enforcement, authorization, evidence, and auditability for autonomous AI systems.",
  keywords: [
    "AI agent governance",
    "AI agent security",
    "AI agent control",
    "AI agent authorization",
    "AI agent permissions",
    "AI agent policy enforcement",
    "AI agent audit trail",
    "AI agent runtime governance",
    "MCP security",
    "agentic AI security",
  ],
  authors: [{ name: "HELIOS Systems" }],
  creator: "HELIOS",
  openGraph: {
    type: "website",
    locale: "en_US",
    url: siteUrl,
    title: "HELIOS — The Control Plane for AI Agents",
    description:
      "Define what AI agents are allowed to do. Control their actions. Prove what actually happened.",
    siteName: "HELIOS",
    images: [
      {
        url: "/assets/helios-logo.jpg",
        width: 1024,
        height: 1024,
        alt: "HELIOS Equipment Plate — Governed AI Command Center",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "HELIOS — The Control Plane for AI Agents",
    description:
      "Define what AI agents are allowed to do. Control their actions. Prove what actually happened.",
    images: ["/assets/helios-logo.jpg"],
  },
  icons: {
    icon: "/assets/helios-mark.svg",
    apple: "/assets/helios-logo.png",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export const viewport: Viewport = {
  themeColor: "#0A0A08",
  colorScheme: "dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
