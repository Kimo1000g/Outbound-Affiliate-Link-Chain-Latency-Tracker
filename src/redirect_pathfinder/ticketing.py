"""Real ticket push — Jira / Linear / ClickUp POST (markdown generation alone ships nothing).

Creds from env (never from the audit payload, never logged):
  JIRA_BASE, JIRA_EMAIL, JIRA_TOKEN, JIRA_PROJECT
  LINEAR_TOKEN (+ LINEAR_TEAM)
  CLICKUP_TOKEN (+ CLICKUP_LIST_ID)
"""
from __future__ import annotations
import os
import httpx


def _jira_cfg() -> dict:
    return {"base": os.environ.get("JIRA_BASE", ""), "email": os.environ.get("JIRA_EMAIL", ""),
            "token": os.environ.get("JIRA_TOKEN", ""), "project": os.environ.get("JIRA_PROJECT", "AFF")}


async def push_jira(summary: str, description: str, priority: str = "High") -> dict:
    cfg = _jira_cfg()
    if not (cfg["base"] and cfg["email"] and cfg["token"]):
        return {"pushed": False, "reason": "JIRA_BASE/EMAIL/TOKEN not set — ticket markdown generated locally only"}
    try:
        async with httpx.AsyncClient(timeout=20, auth=(cfg["email"], cfg["token"])) as c:
            r = await c.post(f"{cfg['base'].rstrip('/')}/rest/api/3/issue",
                             json={"fields": {"project": {"key": cfg["project"]},
                                              "summary": summary[:255], "description": description[:30000],
                                              "issuetype": {"name": "Bug"},
                                              "priority": {"name": "Highest" if priority == "Highest" else "High"}}})
            if r.status_code in (200, 201):
                return {"pushed": True, "key": r.json().get("key", ""), "url": f"{cfg['base'].rstrip('/')}/browse/{r.json().get('key', '')}"}
            return {"pushed": False, "reason": f"Jira HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"pushed": False, "reason": f"{type(e).__name__}: {e}"}


async def push_linear(title: str, description: str) -> dict:
    tok = os.environ.get("LINEAR_TOKEN", "")
    team = os.environ.get("LINEAR_TEAM", "")
    if not tok or not team:
        return {"pushed": False, "reason": "LINEAR_TOKEN/LINEAR_TEAM not set"}
    try:
        async with httpx.AsyncClient(timeout=20, headers={"Authorization": tok}) as c:
            r = await c.post("https://api.linear.app/graphql",
                             json={"query": "mutation($i: IssueCreateInput!){issueCreate(input:$i){success issue{id identifier}}}",
                                   "variables": {"i": {"teamId": team, "title": title[:255], "description": description[:20000]}}})
            j = r.json()
            ok = ((j.get("data") or {}).get("issueCreate") or {}).get("success")
            return {"pushed": bool(ok), "issue": ((j.get("data") or {}).get("issueCreate") or {}).get("issue", {}),
                    "reason": "" if ok else str(j)[:200]}
    except Exception as e:
        return {"pushed": False, "reason": f"{type(e).__name__}: {e}"}
