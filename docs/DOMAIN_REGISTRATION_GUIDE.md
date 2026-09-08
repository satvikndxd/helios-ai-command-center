# Domain Registration & Deployment Guide for HELIOS (helios.is-a.dev)

This guide documents how to claim and configure `helios.is-a.dev` using the official `is-a-dev` registry:
**Repository:** https://github.com/is-a-dev/register.git

---

## 1. The Domain Configuration File

The domain record has been prepared in `domains/helios.json`:

```json
{
  "owner": {
    "username": "satvikndxd",
    "email": "satvikndxd@gmail.com"
  },
  "records": {
    "CNAME": "cname.vercel-dns.com"
  }
}
```

---

## 2. Steps to Register `helios.is-a.dev` via GitHub

1. **Fork the Registry Repository:**
   Go to [https://github.com/is-a-dev/register](https://github.com/is-a-dev/register) and click **Fork**.

2. **Add the Domain Record:**
   - In your forked repository, navigate to the `domains/` directory.
   - Click **Add file** → **Create new file**.
   - Name the file: `helios.json`
   - Paste the contents of `domains/helios.json` (shown above).

3. **Open a Pull Request:**
   - Title: `Register helios.is-a.dev`
   - Description: Include a short note: *"Registration for HELIOS — Governed AI Command Center and Agent Control Plane."*
   - Submit the PR. The automated validation workflow (`CI`) will verify the JSON schema and confirm that `helios.is-a.dev` is available.
   - Once merged by the maintainers, DNS will propagate globally within minutes.

4. **Connect Custom Domain in Vercel:**
   - Go to your Vercel Project Dashboard → **Settings** → **Domains**.
   - Add `helios.is-a.dev`.
   - Vercel will automatically verify the CNAME record (`cname.vercel-dns.com`) and issue a free SSL certificate via Let's Encrypt.
