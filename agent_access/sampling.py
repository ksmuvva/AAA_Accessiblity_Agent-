"""WCAG-EM page sampling (WCAG-EM 1.0, step 3): one page per page template, plus a seeded random sample.

The candidates are the start page's same-site links and the pages the site's /sitemap.xml lists. The page template
is guessed from the URL's shape (template()): pages that differ only in an id or a slug share one. The structured
sample is the start page and the first page of every other template, in link order; the random sample adds 10% of
it (at least one page), drawn with the seed from the pages left. The whole sample is capped at `cap` pages. The
report states the method, the seed, the templates and both parts, so a run can be repeated.
"""

from __future__ import annotations

import html
import math
import random
import re
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse

from agent_access.agent_tools.browser_tools import _NON_PAGE_EXT, refused, same_site_links
from agent_access.context import AuditContext
from agent_access.intent import irreversible

METHOD = ("WCAG-EM 1.0 step 3: the start page and one page per URL template (structured sample), plus a random "
          "10% of that, at least one page, drawn with the seed")
INSTANCE = re.compile(r"\d|^[0-9a-f]{8,}$", re.I)   # a path segment with a digit or a long hex id names one instance
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
MAX_CANDIDATES = 2000
MAX_SITEMAPS = 5   # child sitemaps of a sitemap index that are read


def template(url: str) -> str:
    """The page template a URL's shape suggests: /products/12 and /products/7 share "/products/{id}", /blog/a-post and
    /blog/another "/blog/{slug}" (the last segment of a deeper path); query keys are kept, their values dropped."""
    parsed = urlparse(url)
    shape = ["{id}" if INSTANCE.search(p) else p for p in parsed.path.split("/") if p]
    if len(shape) >= 2 and shape[-1] != "{id}":
        shape[-1] = "{slug}"
    keys = sorted({k for k, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    return "/" + "/".join(shape) + ("?" + "&".join(keys) if keys else "")


async def sitemap_urls(audit: AuditContext, origin: str) -> list[str]:
    """The URLs /sitemap.xml lists, following a sitemap index to its first MAX_SITEMAPS sitemaps. A site without one
    (or one that fails) gives none: the links still make the sample. A sitemap is the site's content, so every fetch,
    a child sitemap's or a redirect's, must be on the audited site and pass browser_tools.refused."""
    request = (await audit.page()).context.request
    queue, urls = [f"{origin}/sitemap.xml"], []
    for _ in range(1 + MAX_SITEMAPS):
        if not queue:
            break
        url = queue.pop(0)
        if urlparse(url).netloc != urlparse(origin).netloc or refused(audit, url):
            continue
        try:
            response = await request.get(url, timeout=10_000, max_redirects=0)
            if 300 <= response.status < 400 and response.headers.get("location"):
                queue.insert(0, urljoin(url, response.headers["location"]))   # checked like the rest before fetching
                continue
            body = await response.text() if response.ok else ""
        except Exception:
            body = ""
        for loc in map(html.unescape, LOC.findall(body)):
            (queue if loc.lower().endswith(".xml") else urls).append(urldefrag(loc)[0])
    return urls


async def wcag_em_sample(audit: AuditContext, cap: int, seed: int) -> dict:
    """The sample for the loaded start page (see the module docstring); `pages` lists it in audit order."""
    start, origin = audit.current_url, urlparse(audit.current_url)

    def candidate(url: str) -> bool:
        parsed = urlparse(url)
        return (parsed.scheme in ("http", "https") and parsed.netloc == origin.netloc and url.rstrip("/") != start.rstrip("/")
                and not _NON_PAGE_EXT.search(parsed.path) and not parsed.path.lower().endswith(".gz") and not irreversible(url))

    links = [link["url"] for link in (await same_site_links(audit, MAX_CANDIDATES)).get("links", [])]
    listed = await sitemap_urls(audit, f"{origin.scheme}://{origin.netloc}") if origin.scheme in ("http", "https") else []
    candidates = [u for u in dict.fromkeys(links + listed) if candidate(u)][:MAX_CANDIDATES]
    templates: dict[str, list[str]] = {}
    for url in [start] + candidates:
        templates.setdefault(template(url), []).append(url)
    structured = [urls[0] for urls in templates.values()][:cap]   # the start page first
    while len(structured) > 1 and len(structured) + math.ceil(len(structured) / 10) > cap:
        structured.pop()   # keep room within the cap for the random tenth of what stays
    k = math.ceil(len(structured) / 10)
    rest = [u for u in candidates if u not in structured]
    drawn = random.Random(seed).sample(rest, min(k, len(rest), cap - len(structured)))
    return {"method": METHOD, "seed": seed, "cap": cap, "candidates": len(candidates), "from_sitemap": len(listed),
            "templates": {t: len(urls) for t, urls in templates.items()}, "structured": structured, "random": drawn,
            "pages": structured + drawn}
