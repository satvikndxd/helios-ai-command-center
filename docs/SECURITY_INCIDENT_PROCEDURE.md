# HELIOS Security Incident & Breach Response Procedure

**Classification:** Internal Operational Directive  
**Document ID:** HL-SOP-SEC-001  
**Last Revised:** September 2026  
**Applicability:** HELIOS Public Landing Page, Waitlist Submissions & Operator Infrastructure  

---

## 1. Incident Response Lifecycle

In the event of an anomalous security event, credential compromise, or suspected data breach affecting waitlist records or intake infrastructure, follow this sequential execution protocol:

```
[SECURITY INCIDENT DETECTED]
             │
             ▼
      [01. CONTAIN]
  Isolate compromised service, revoke keys, block attacker IPs
             │
             ▼
   [02. ROTATE CREDENTIALS]
  Rotate RESEND_API_KEY, deployment tokens, and operator accounts
             │
             ▼
 [03. IDENTIFY AFFECTED DATA]
  Audit logs, query mailer receipts, isolate affected email records
             │
             ▼
[04. PRESERVE REQUIRED EVIDENCE]
  Snapshot logs, hash records, preserve RFC headers for forensic analysis
             │
             ▼
[05. ASSESS OBLIGATIONS]
  Review notification requirements under DPDP Rules 2025 and applicable law
             │
             ▼
     [06. REMEDIATE]
  Deploy code patch, strengthen rate limiters, patch dependencies
             │
             ▼
     [07. DOCUMENT]
  Compile post-incident review (PIR) and archive remediation trail
```

---

## 2. Immediate Operational Steps

### Phase 1: Containment
1. If the serverless intake is experiencing an ongoing exploit, disable the endpoint temporarily via Vercel dashboard or set an emergency intake freeze flag.
2. Terminate any active unauthorized sessions.

### Phase 2: Credential Invalidation & Rotation
1. **Resend API Key**: Immediately revoke the compromised key in Resend dashboard and generate a fresh key with restricted sending permissions.
2. **Hosting Tokens**: Invalidate Vercel deployment tokens and personal access tokens.
3. **Operator Mailbox**: Verify multi-factor authentication (MFA) on `satvikndxd@gmail.com`.

### Phase 3: Data Scope Assessment
- Identify whether submitted applicant email addresses were exposed.
- Remember: Waitlist data is strictly minimal (email only, no passwords, no financial info, no names).

### Phase 4: Data Subject Notification (Where Required)
- If personal data of applicants is confirmed compromised, notify affected applicants without unreasonable delay via their submitted email addresses with plain-language details and recommended actions.

### Phase 5: Incident Contact
- Incident Lead: Satvik Anand (`satvikndxd@gmail.com`)
