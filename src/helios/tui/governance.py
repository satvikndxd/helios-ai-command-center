"""
TUI governance control center — the governance-aware views.

/systems /system /models /decisions /why /changes /drift /audit /governance

Everything rendered is backed by real API state (the four planes). This is
the "control center" surface: identity, policy, evidence, assurance.
"""

from __future__ import annotations

from helios.tui import ui
from helios.tui.ui import badge, bullet, c, error, kv, panel, risk_badge, table


_STATUS_MARK = {"pass": ("✓", "green"), "warn": ("⚠", "yellow"),
                "fail": ("✗", "red"), "na": ("·", "dim")}


def _score_bar(overall: int) -> str:
    blocks = round(overall / 10)
    color = "green" if overall >= 80 else "yellow" if overall >= 60 else "red"
    return c("▰" * blocks, color) + c("▱" * (10 - blocks), "dim") + f"  {overall}/100"


class GovernancePane:
    """Governance views over /v1/systems, /v1/models, /v1/decisions, /v1/changes."""

    def __init__(self, call):
        self._call = call  # (method, path, payload=None) -> dict | None
        self.system: str | None = None

    # -- systems -----------------------------------------------------------

    def list_systems(self) -> None:
        data = self._call("GET", "/v1/systems")
        if not data:
            return
        rows = []
        for s in data.get("systems", []):
            marker = c("●", "green") if s["system_id"] == self.system else c("·", "dim")
            rows.append([marker, s["system_id"], s["owner"],
                         s["environment"], f"L{s['autonomy_level']}",
                         risk_badge(s["risk_class"]), s["lifecycle"]])
        print(table(["", "system", "owner", "env", "autonomy", "risk", "lifecycle"], rows))

    def use_system(self, system_id: str) -> None:
        data = self._call("GET", f"/v1/systems/{system_id}")
        if data:
            self.system = system_id
            print(ui.success(f"Active system: {c(system_id, 'fg', bold=True)}"))

    def show_system(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        if not system_id:
            print(error("No system — /systems then /system <id>"))
            return
        system = self._call("GET", f"/v1/systems/{system_id}")
        score = self._call("GET", f"/v1/systems/{system_id}/governance")
        drift = self._call("GET", f"/v1/systems/{system_id}/drift")
        oversight = self._call("GET", f"/v1/systems/{system_id}/oversight")
        if not system or not score:
            return
        self.system = system_id
        model_line = ", ".join(
            f"{m.get('provider')}/{m.get('model_id')}" for m in (system.get("models") or [])
        ) or "none declared"
        drift_badge = (badge("⚠ DETECTED", "red") if drift and drift.get("drift_detected")
                       else c("none", "dim"))
        violations = len(oversight.get("bypassed_oversight", [])) if oversight else 0
        print(panel(f"{system_id.upper()} · AI GOVERNANCE", [
            kv("owner", c(system["owner"], "fg", bold=True)),
            kv("purpose", c(system["purpose"] or "—", "fg")),
            kv("risk", risk_badge(system["risk_class"])),
            kv("autonomy", c(f"L{system['autonomy_level']} — "
                             f"{system['autonomy_label']}", "fg")),
            kv("environment", c(system["environment"], "fg", bold=True)),
            kv("lifecycle", c(system["lifecycle"], "fg")),
            kv("models", c(model_line, "fg")),
            kv("data", c(", ".join(system.get("data_classes") or []) or "public", "fg")),
            kv("policies", c(str(len(system.get("policies") or [])) + " declared", "fg")),
            kv("governance", _score_bar(score["overall"])),
            kv("drift", drift_badge),
            kv("bypasses", c(str(violations),
                             "red" if violations else "green")),
        ]))

    # -- governance score --------------------------------------------------

    def show_governance(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        if not system_id:
            print(error("No system selected."))
            return
        score = self._call("GET", f"/v1/systems/{system_id}/governance")
        if not score:
            return
        print(panel(f"{system_id.upper()} · GOVERNANCE {score['overall']}/100",
                    [kv("score", _score_bar(score["overall"]))]))
        for check in score["checks"]:
            mark, color = _STATUS_MARK.get(check["status"], ("·", "dim"))
            print(f"  {c(mark, color)} {c(check['label'].ljust(20), 'fg')}"
                  f"{c(check['detail'], 'dim')}")
        print(c(f"  {score['explanation']}", "dim"))

    # -- models ------------------------------------------------------------

    def list_models(self) -> None:
        data = self._call("GET", "/v1/models")
        if not data:
            return
        rows = []
        for m in data.get("models", []):
            status_color = {"approved": "green", "proposed": "yellow",
                            "revoked": "red"}.get(m["status"], "dim")
            rows.append([badge(m["status"], status_color),
                         f"{m['provider']}/{m['model_id']}", f"v{m['version']}",
                         risk_badge(m["risk_class"]),
                         ",".join(m["allowed_data_classes"]),
                         ",".join(m["allowed_environments"])])
        print(table(["status", "model", "ver", "risk", "data classes", "envs"], rows))

    # -- decisions + why ---------------------------------------------------

    def list_decisions(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        path = "/v1/decisions"
        if system_id:
            path += f"?system_id={system_id}"
        data = self._call("GET", path)
        if not data:
            return
        rows = []
        for d in data.get("decisions", []):
            dec_color = {"allow": "green", "deny": "red",
                         "require_approval": "yellow",
                         "require_human_review": "yellow"}.get(d["decision"], "dim")
            rows.append([c(d["id"][:8], "dim"), d["kind"], d["action"][:28],
                         risk_badge(d["risk"] or "low"),
                         badge(d["decision"], dec_color),
                         d["human_oversight"]])
        print(table(["id", "kind", "action", "risk", "decision", "oversight"], rows))
        print(c("  /why <decision-id> for the full explanation", "dim"))

    def why(self, decision_id: str) -> None:
        # resolve short-id prefix against recent decisions
        if len(decision_id) < 36:
            data = self._call("GET", "/v1/decisions?limit=200")
            matches = [d["id"] for d in (data or {}).get("decisions", [])
                       if d["id"].startswith(decision_id)]
            if len(matches) == 1:
                decision_id = matches[0]
            elif not matches:
                print(error(f"no decision matches '{decision_id}'"))
                return
            else:
                print(error(f"{len(matches)} decisions match — use the full id"))
                return
        why = self._call("GET", f"/v1/decisions/{decision_id}/why")
        if not why:
            return
        color = {"ALLOWED": "green", "BLOCKED": "red"}.get(why["verdict"], "yellow")
        lines = [c(why["question"], "fg", bold=True), ""]
        for check in why["checks"]:
            mark = "✓" if check["ok"] else "✗"
            mark_color = "green" if check["ok"] else "red"
            lines.append(f"{c(mark, mark_color)} {c(check['label'], 'fg')}: "
                         f"{c(check['detail'], 'dim')}")
        lines.append("")
        lines.append(kv("verdict", badge(why["verdict"], color)))
        lines.append(kv("reason", c(why["reason"], "fg")))
        print(panel("WHY", lines, color=color))

    # -- changes -----------------------------------------------------------

    def list_changes(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        path = "/v1/changes"
        if system_id:
            path += f"?system_id={system_id}"
        data = self._call("GET", path)
        if not data:
            return
        rows = []
        for ch in data.get("changes", []):
            rows.append([c(ch["id"][:8], "dim"), ch["kind"], ch["title"][:34],
                         ch["author"][:14], badge(ch["status"], "sea")])
        print(table(["id", "kind", "title", "author", "status"], rows))

    # -- drift + audit -----------------------------------------------------

    def show_drift(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        if not system_id:
            print(error("No system selected."))
            return
        drift = self._call("GET", f"/v1/systems/{system_id}/drift")
        if not drift:
            return
        if not drift["drift_detected"]:
            print(ui.success(f"{system_id}: within baseline "
                             f"({drift.get('note', 'no drift')})"))
            return
        lines = [c("GOVERNANCE DRIFT DETECTED", "red", bold=True), ""]
        for signal in drift["signals"]:
            lines.append(bullet(c(signal["detail"], "yellow")))
        print(panel("DRIFT", lines, color="red"))

    def baseline(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        if not system_id:
            print(error("No system selected."))
            return
        data = self._call("POST", f"/v1/systems/{system_id}/baseline")
        if data:
            print(ui.success(f"Baseline captured for {system_id} "
                             f"(sample {data['sample_size']})"))

    def show_audit(self, system_id: str | None = None) -> None:
        system_id = system_id or self.system
        if not system_id:
            print(error("No system selected."))
            return
        report = self._call("GET", f"/v1/systems/{system_id}/audit")
        if not report:
            return
        gs = report["governance_status"]
        print(panel(f"{system_id.upper()} · AUDIT RECORD", [
            kv("owner", c(report["system"]["owner"], "fg")),
            kv("governance", _score_bar(gs["overall"])),
            kv("models", c(str(len(report["models"])) + " declared", "fg")),
            kv("violations", c(str(len(report["violations"])),
                               "red" if report["violations"] else "green")),
            kv("bypasses", c("yes" if report["bypass_detected"] else "no",
                             "red" if report["bypass_detected"] else "green")),
            kv("drift", c("detected" if report["drift"]["detected"] else "none",
                          "red" if report["drift"]["detected"] else "green")),
            kv("changes", c(str(len(report["recent_changes"])), "fg")),
        ]))
        print(c("  " + report["disclaimer"], "dim"))
