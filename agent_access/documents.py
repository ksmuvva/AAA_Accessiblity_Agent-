"""Linked PDFs (plan 3.1): the same-site PDFs the audited pages link to, fetched with a GET and validated against
PDF/UA-1 by the veraPDF CLI when it is on PATH (it needs Java). Each document is its own item in the report, its failed
rules mapped to the WCAG criteria they bear on. Documents aren't merged into the pages' criteria, and no model judges
them yet (whether alt text is meaningful, the reading order).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from agent_access.agent_tools.browser_tools import refused
from agent_access.context import AuditContext, is_state_key

MAX_DOCUMENTS = 10
MAX_BYTES = 20_000_000
# PDF/UA-1 rule tags -> the WCAG criterion a failure bears on. Fonts, syntax and the PDF/UA identifier bear on none.
CRITERIA_BY_TAG = {"alt-text": "1.1.1", "figure": "1.1.1", "structure": "1.3.1", "table": "1.3.1", "list": "1.3.1",
                   "heading": "1.3.1", "artifact": "1.3.1", "toc": "1.3.1", "note": "1.3.1", "text": "1.3.1",
                   "lang": "3.1.1", "annotation": "4.1.2", "page": "2.4.3"}


def verapdf_command() -> list[str] | None:
    path = shutil.which("verapdf") or shutil.which("verapdf.bat")
    return [path] if path else None


def linked_pdfs(audit: AuditContext) -> list[str]:
    """Same-site links to PDFs on the audited pages (not their states, which link to the same), in link order."""
    urls: list[str] = []
    for key in audit.pages:
        seen = audit.observations.get(key, {}).get("page")
        for link in seen.data.get("links", []) if seen is not None and not is_state_key(key) else []:
            url = link["href"].split("#")[0]
            parsed = urlparse(url)
            if (parsed.scheme in ("http", "https") and parsed.path.lower().endswith(".pdf") and url not in urls
                    and refused(audit, url) is None):
                urls.append(url)
    return urls[:MAX_DOCUMENTS]


def _criteria(rule: dict) -> list[str]:
    tags = set(rule.get("tags", []))
    found = {CRITERIA_BY_TAG[t] for t in tags if t in CRITERIA_BY_TAG}
    if "metadata" in tags and rule.get("clause") == "7.1":   # the metadata stream and its dc:title
        found.add("2.4.2")
    return sorted(found)


def _document(url: str, job: dict) -> dict:
    if not job.get("validationResult"):
        why = (job.get("taskException") or {}).get("exception") or "no result"
        return {"url": url, "checked": False, "reason": f"veraPDF could not read it: {str(why)[:120]}"}
    result = job["validationResult"][0]
    details = result.get("details", {})
    return {"url": url, "checked": True, "profile": result.get("profileName", "PDF/UA-1"),
            "compliant": bool(result.get("compliant")), "passed_rules": details.get("passedRules", 0),
            "failed_rules": [{"clause": r.get("clause"), "test": r.get("testNumber"), "checks": r.get("failedChecks", 0),
                              "description": " ".join(str(r.get("description", "")).split())[:300],
                              "criteria": _criteria(r)}
                             for r in details.get("ruleSummaries", []) if r.get("status") == "failed"]}


def _validate(command: list[str], files: list[str]) -> dict:
    """veraPDF's JSON report on `files` (an argument list, no shell). Its exit code is non-zero for a non-compliant
    file, so only its output decides."""
    run = subprocess.run([*command, "--flavour", "ua1", "--format", "json", *files], capture_output=True,
                         timeout=60 + 30 * len(files))
    return json.loads(run.stdout)["report"]


async def check_documents(audit: AuditContext) -> tuple[list[dict], list[str]]:
    """Each linked PDF's result, and a warning for the ones that weren't checked. veraPDF's version goes to
    audit.engines."""
    urls = linked_pdfs(audit)
    if not urls:
        return [], []
    if (command := verapdf_command()) is None:
        return ([{"url": u, "checked": False, "reason": "veraPDF isn't installed"} for u in urls],
                [f"{len(urls)} linked PDF(s) were not checked: veraPDF isn't installed (it needs Java; put verapdf on PATH)."])
    docs, files = [], {}
    request = (await audit.page()).context.request
    with tempfile.TemporaryDirectory() as tmp:
        for i, url in enumerate(urls):
            try:
                response = await request.get(url, timeout=30_000)
                body = await response.body()
            except Exception as exc:   # one document must not stop the others
                docs.append({"url": url, "checked": False, "reason": f"not downloaded: {type(exc).__name__}"})
                continue
            if response.status != 200 or not body.startswith(b"%PDF") or len(body) > MAX_BYTES:
                why = (f"HTTP {response.status}" if response.status != 200 else
                       "not a PDF" if not body.startswith(b"%PDF") else f"over {MAX_BYTES // 1_000_000} MB")
                docs.append({"url": url, "checked": False, "reason": why})
                continue
            path = Path(tmp) / f"document{i}.pdf"
            path.write_bytes(body)
            files[str(path)] = url
        if files:
            try:
                report = await asyncio.to_thread(_validate, command, list(files))
            except Exception as exc:   # a timeout, no Java, output that isn't its JSON
                docs += [{"url": u, "checked": False, "reason": f"veraPDF failed: {type(exc).__name__}"} for u in files.values()]
            else:
                apps = next((r for r in report.get("buildInformation", {}).get("releaseDetails", []) if r.get("id") == "apps"), {})
                audit.engines["veraPDF"] = f"veraPDF {apps.get('version', '?')} (PDF/UA-1, linked PDFs)"
                docs += [_document(files[job["itemDetails"]["name"]], job) for job in report.get("jobs", [])]
    unchecked = [d for d in docs if not d["checked"]]
    warnings = [f"{len(unchecked)} linked PDF(s) were not checked: "
                + "; ".join(f"{d['url']} ({d['reason']})" for d in unchecked[:5]) + "."] if unchecked else []
    return sorted(docs, key=lambda d: urls.index(d["url"])), warnings
