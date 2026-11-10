#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Check the references in a Markdown reading list against their source records.

check_md_refs.py FILE.md [--notes]

Reads list items shaped like

    - Author, Author and Author (Year). [Title](url). *Venue* Vol(Issue), pages.
    - Author et al. (Year). [Title](url). *Venue*.
    - Author and Author, eds. (Year). [Title](url). Publisher.
    - Author (Year). *Book Title: Subtitle*. Publisher.
    - Author (Year). [Title](url). In ... Reprinted in *Venue* Vol, pages (Year).

and looks each one up: doi.org links in Crossref, arxiv.org links in the arXiv API, other
links in the page's citation_* or DC.* meta tags, unlinked *italic* titles (books) in
OpenLibrary. Compares title, authors (editors for ", eds."), year, volume and pages.

Prints one line per entry that needs a look:
    mismatch    the source disagrees with the entry
    unverified  the source could not be read: network or HTTP error, a bot check (reported,
                never worked around), no record, or a page without citation metadata
    unlinked    no link and not a book, so nothing to check against
With --notes, also prints accepted differences: the source records only the first page,
the arXiv version is a year older than the venue, the link is to a later reprint, and so on.
"et al." should stand for three or more authors. Exit status 1 if any entry is a mismatch.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser

HEADERS = {
    "User-Agent": "scholar-kit-check-refs/1.0 (+https://github.com/yy/scholar-kit)"
}
TIMEOUT = 20
RETRY = {429, 500, 502, 503, 504}
BOT_CHECK = re.compile(
    r"verifying your browser|not a bot|just a moment|attention required"
    r"|checking your browser|are you a robot|captcha",
    re.IGNORECASE,
)

ENTRY = re.compile(
    r"^\s*[-*] (?P<authors>.+?) \((?P<year>\d{4})[a-z]?\)\. (?P<rest>.+?)\s*$"
)
LINK = re.compile(r"^\[(?P<title>.+?)\]\((?P<url>[^)\s]+)\)(?P<tail>.*)$")
BOOK = re.compile(r"^\*(?P<title>[^*]+)\*(?P<tail>.*)$")
TAIL = re.compile(
    r"\*(?P<venue>[^*]+)\*(?: (?P<vol>\d+)(?:\([^)]*\))?)?(?:, (?P<pages>[^.(]+?))?"
    r"(?: \((?P<reprint>\d{4})\))?\.?\s*$"
)
ARXIV = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>[^?#\s]+?)(?:v\d+)?(?:\.pdf)?/?$", re.IGNORECASE
)
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(?P<id>.+)$", re.IGNORECASE)


# --- Entries -----------------------------------------------------------------


@dataclass
class Entry:
    line: int
    authors: str
    year: int
    title: str
    url: str | None = None
    book: bool = False
    venue: str | None = None
    vol: str | None = None
    pages: str | None = None
    reprint: int | None = None

    @property
    def label(self) -> str:
        return f"L{self.line} {self.authors} ({self.year})"


def parse_entries(text: str) -> list[Entry]:
    entries = []
    for n, line in enumerate(text.splitlines(), 1):
        m = ENTRY.match(line)
        if not m:
            continue
        e = Entry(n, m["authors"], int(m["year"]), "")
        rest = m["rest"]
        if lm := LINK.match(rest):
            e.title, e.url, tail = lm["title"], lm["url"], lm["tail"]
        elif bm := BOOK.match(rest):
            e.title, e.book, tail = bm["title"], True, ""
        else:
            e.title, tail = rest.split(". ")[0], ""
        if tm := TAIL.search(tail):
            e.venue, e.vol, e.pages = tm["venue"], tm["vol"], tm["pages"]
            e.reprint = int(tm["reprint"]) if tm["reprint"] else None
        entries.append(e)
    return entries


def named_authors(s: str) -> tuple[list[str], bool, bool]:
    """Split 'A, B and C', 'A et al.' or 'A and B, eds.' into (names, et_al, editors)."""
    eds = bool(re.search(r",? eds?\.$", s))
    s = re.sub(r",? eds?\.$", "", s)
    etal = s.endswith(" et al.")
    if etal:
        s = s[: -len(" et al.")]
    names = [p.strip() for p in re.split(r", (?:and )?| and | & ", s) if p.strip()]
    return names, etal, eds


# --- Names and titles --------------------------------------------------------


def norm(s: str) -> str:
    s = html.unescape(s).replace("&", " and ")
    s = re.sub(r"<[^>]+>", " ", s)  # Crossref titles can carry markup
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def main_title(t: str) -> str:
    return t.split(": ", 1)[0]


def family(name: str) -> str:
    """Family name from 'First Last', 'Last, First' or OpenLibrary's 'First Last - note'."""
    name = name.strip()
    name = re.sub(r"\s+-\s+.*$", "", name)
    if "," in name:
        return name.split(",")[0].strip()
    parts = name.split()
    while len(parts) > 1 and parts[-1].rstrip(".").lower() in {"jr", "sr", "ii", "iii"}:
        parts.pop()
    return parts[-1] if parts else ""


def same_name(a: str, b: str) -> bool:
    x, y = norm(a).split(), norm(b).split()
    return bool(x and y) and (x[-1] == y[-1] or set(x) <= set(y) or set(y) <= set(x))


# --- Source records ----------------------------------------------------------


@dataclass
class Record:
    title: str
    authors: list[str]  # family names
    years: set[int]
    editors: list[str] = field(default_factory=list)
    vol: str | None = None
    pages: str | None = None
    article_id: bool = False  # `pages` is an article ID equal to the DOI suffix


class Unverified(Exception):
    """The source could not be read or has no record; the message says why."""


@dataclass
class Page:
    url: str  # after redirects
    status: int
    text: str


def is_bot_check(page: Page) -> bool:
    t = re.search(r"<title[^>]*>(.*?)</title>", page.text, re.DOTALL | re.IGNORECASE)
    title = html.unescape(t.group(1)) if t else ""
    path = urllib.parse.urlsplit(page.url).path
    return bool(BOT_CHECK.search(title)) or path.startswith("/challenge")


def _decode(r) -> str:
    charset = r.headers.get_content_charset() or "utf-8"
    return r.read().decode(charset, "replace")


_host_locks: dict[str, threading.Lock] = {}


def fetch(url: str, tries: int = 4) -> Page:
    """GET a URL, one request at a time per host. HTTP errors come back as a Page;
    network failures raise Unverified. Retries network errors and 429/5xx (honoring
    Retry-After), but never a bot check."""
    host = urllib.parse.urlsplit(url).netloc
    with _host_locks.setdefault(host, threading.Lock()):  # atomic
        for i in range(tries):
            wait = 2**i
            try:
                req = urllib.request.Request(url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return Page(r.url, r.status, _decode(r))
            except urllib.error.HTTPError as e:
                try:
                    body = _decode(e)
                except Exception:  # noqa: BLE001
                    body = ""
                page = Page(e.url or url, e.code, body)
                if e.code not in RETRY or is_bot_check(page) or i == tries - 1:
                    return page
                after = (e.headers.get("Retry-After") or "").strip()
                wait = min(int(after), 30) if after.isdigit() else wait
            except (urllib.error.URLError, OSError) as e:
                if i == tries - 1:
                    reason = getattr(e, "reason", None) or e
                    raise Unverified(f"could not fetch {url}: {reason}") from None
            time.sleep(wait)
    raise AssertionError("unreachable")


def text_of(page: Page) -> str:
    if is_bot_check(page):
        host = urllib.parse.urlsplit(page.url).netloc
        raise Unverified(f"could not fetch: bot check at {host} (check by hand)")
    if page.status >= 400:
        raise Unverified(f"could not fetch: HTTP {page.status} from {page.url}")
    return page.text


def _year(parts: dict | None) -> int | None:
    dp = (parts or {}).get("date-parts") or [[None]]
    return dp[0][0] if dp and dp[0] else None


def crossref_record(msg: dict, doi: str) -> Record:
    dated = [msg.get(k) for k in ("issued", "published-print", "published-online")]
    issue = msg.get("journal-issue") or {}
    dated += [issue.get(k) for k in ("published-print", "published-online")]
    title = (msg.get("title") or [""])[0]
    subtitle = (msg.get("subtitle") or [""])[0]
    page, number = msg.get("page"), msg.get("article-number")
    suffix = doi.split("/", 1)[-1].lower()

    def names(people):
        return [p.get("family") or p.get("name", "") for p in people or []]

    return Record(
        title=f"{title}: {subtitle}" if subtitle else title,
        authors=names(msg.get("author")),
        editors=names(msg.get("editor")),
        years={y for y in map(_year, dated) if y},
        vol=msg.get("volume"),
        pages=page or number,
        article_id=bool(not page and number and number.lower() == suffix),
    )


def crossref(doi: str, get) -> Record:
    page = get("https://api.crossref.org/works/" + urllib.parse.quote(doi))
    if page.status == 404:
        raise Unverified(f"no Crossref record for {doi}")
    return crossref_record(json.loads(text_of(page))["message"], doi)


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.m: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        key = (a.get("name") or a.get("property") or "").lower()
        # Some sites escape twice (JMLR: "Radovanovi&amp;#263;").
        content = " ".join(html.unescape(a.get("content") or "").split())
        if key and content:
            self.m.setdefault(key, []).append(content)


def page_record(text: str) -> Record:
    p = _Meta()
    p.feed(text)
    m = p.m

    def first(*keys):
        return next((m[k][0] for k in keys if m.get(k)), "")

    authors = m.get("citation_author") or m.get("dc.creator") or []
    if len(authors) == 1 and ";" in authors[0]:  # JASSS: "Last, First; Last, First"
        authors = authors[0].split(";")
    title = first("citation_title", "dc.title")
    if not title and not authors:
        raise Unverified("page has no citation metadata")
    date = first(
        "citation_publication_date", "citation_date", "dc.date.issued", "dc.date"
    )
    fp, lp = first("citation_firstpage"), first("citation_lastpage")
    return Record(
        title=title,
        authors=[family(a) for a in authors if a.strip()],
        years={int(y) for y in re.findall(r"\b(?:1[5-9]|20)\d\d\b", date)[:1]},
        vol=first("citation_volume") or None,
        pages=(f"{fp}-{lp}" if lp and lp != fp else fp) or None,
    )


def arxiv_id(url: str) -> str | None:
    if m := ARXIV.search(url):
        return m["id"]
    if "doi.org/" in url and (m := ARXIV_DOI.match(doi_of(url))):
        return m["id"]
    return None


def doi_of(url: str) -> str:
    return urllib.parse.unquote(url.split("doi.org/", 1)[1])


def arxiv_records(xml: str) -> dict[str, Record]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = {}
    for e in ET.fromstring(xml).findall("a:entry", ns):
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).split("/abs/")[-1])
        published = e.findtext("a:published", "", ns)
        if not aid or not published:  # error entries carry no date
            continue
        out[aid] = Record(
            title=" ".join(e.findtext("a:title", "", ns).split()),
            authors=[family(n.text or "") for n in e.findall("a:author/a:name", ns)],
            years={int(published[:4])},
        )
    return out


def arxiv(ids: list[str], get) -> dict[str, Record | str]:
    """Records by arXiv ID; a string says why there is none."""
    out: dict[str, Record | str] = {}
    ids = list(dict.fromkeys(ids))
    url = "https://export.arxiv.org/api/query?max_results=100&id_list="
    for i in range(0, len(ids), 100):  # 100 per call, 3 s apart, as arXiv asks
        chunk = ids[i : i + 100]
        if i:
            time.sleep(3)
        try:
            recs: dict[str, Record | str] = dict(
                arxiv_records(text_of(get(url + ",".join(chunk))))
            )
        except Unverified as e:
            recs = dict.fromkeys(chunk, str(e))
        for a in chunk:
            out[a] = recs.get(a) or f"no arXiv record for {a}"
    return out


def openlibrary_record(docs: list[dict], e: Entry) -> Record | None:
    """Pick the works matching the title and first author; accept any edition's year."""
    ours = named_authors(e.authors)[0]

    def full(d):
        return d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else "")

    def title_ok(d):
        return (
            max(sim(e.title, full(d)), sim(main_title(e.title), d.get("title", "")))
            >= 0.9
        )

    def n_matched(d):
        theirs = [family(a) for a in d.get("author_name", [])]
        return sum(any(same_name(o, t) for t in theirs) for o in ours)

    def first_ok(d):
        return any(same_name(ours[0], family(a)) for a in d.get("author_name", []))

    hits = [d for d in docs if title_ok(d) and first_ok(d)]
    if not hits:
        return None
    best = max(hits, key=lambda d: (n_matched(d), sim(e.title, full(d))))
    years = set()
    for d in hits:
        years |= set(d.get("publish_year") or []) | {d.get("first_publish_year")}
    return Record(
        title=full(best),
        authors=[family(a) for a in best.get("author_name", [])],
        years={y for y in years if y},
    )


def openlibrary(e: Entry, get) -> Record:
    first = named_authors(e.authors)[0][0]
    fields = "title,subtitle,author_name,first_publish_year,publish_year"
    queries = [  # field search first; free text tolerates "&" vs "and"
        {"title": main_title(e.title), "author": first},
        {"q": f"{main_title(e.title)} {first}"},
    ]
    for q in queries:
        qs = urllib.parse.urlencode({**q, "fields": fields, "limit": 20})
        docs = json.loads(text_of(get("https://openlibrary.org/search.json?" + qs)))
        if rec := openlibrary_record(docs.get("docs", []), e):
            return rec
    raise Unverified("no matching book in OpenLibrary")


# --- Comparison --------------------------------------------------------------


@dataclass
class Result:
    entry: Entry
    source: str
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unverified: str | None = None

    @property
    def kind(self) -> str:
        if self.source == "none":
            return "unlinked"
        if self.unverified:
            return "unverified"
        return "mismatch" if self.problems else "ok"


def compare(e: Entry, rec: Record, source: str) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    notes: list[str] = []

    # Title
    s = max(sim(e.title, rec.title), sim(e.title, main_title(rec.title)))
    if s < 0.9:
        if sim(main_title(e.title), main_title(rec.title)) < 0.9:
            problems.append(f"title {s:.2f} similar; source: {rec.title!r}")
        elif ": " in rec.title:  # a source without any subtitle is common (OpenLibrary)
            notes.append(f"subtitle differs; source: {rec.title!r}")

    # Authors or editors
    ours, etal, eds = named_authors(e.authors)
    theirs = rec.editors if eds and rec.editors else rec.authors
    if not theirs and rec.editors:
        problems.append(
            f"source lists editors, not authors: {rec.editors} (add ', eds.')"
        )
    elif not theirs:
        problems.append("no author list in source")
    elif not same_name(ours[0], theirs[0]):
        problems.append(f"first author {ours[0]!r} vs source {theirs[0]!r}")
    elif etal:
        if len(theirs) < 3:
            problems.append(f"'et al.' but source has {len(theirs)} authors: {theirs}")
    elif len(ours) != len(theirs):
        problems.append(
            f"{len(ours)} authors named vs {len(theirs)} in source: {theirs}"
        )
    else:
        for o, t in zip(ours, theirs):
            if not same_name(o, t):
                problems.append(f"author {o!r} vs source {t!r}")

    # Year
    if rec.years and e.year not in rec.years:
        later = e.venue and "arxiv" not in e.venue.lower()
        if e.reprint and e.reprint in rec.years:
            notes.append(f"link is to the {e.reprint} reprint of the {e.year} original")
        elif source == "arxiv" and later and e.year - 1 in rec.years:
            notes.append(f"arXiv version is from {e.year - 1}, {e.venue} {e.year}")
        else:
            problems.append(f"year {e.year} vs source {sorted(rec.years)}")

    # Volume and pages
    if e.vol and rec.vol and e.vol.lstrip("0") != str(rec.vol).lstrip("0"):
        problems.append(f"volume {e.vol} vs source {rec.vol}")
    if e.pages and rec.pages and not rec.article_id:
        a = e.pages.replace("–", "-").replace("—", "-").lower().strip()
        b = str(rec.pages).replace("–", "-").lower().strip()
        a1, _, _ = a.partition("-")
        b1, _, b2 = b.partition("-")
        if a in (b, b1) or (b1 == b2 and a == b1):
            pass
        elif a1 == b1 and not b2:
            notes.append(f"source records only the first page ({rec.pages})")
        else:
            problems.append(f"pages {e.pages} vs source {rec.pages}")
    return problems, notes


def source_of(e: Entry) -> str:
    if not e.url:
        return "openlibrary" if e.book else "none"
    if arxiv_id(e.url):
        return "arxiv"
    return "crossref" if "doi.org/" in e.url else "page"


def check(entries: list[Entry], get=None, workers: int = 4) -> list[Result]:
    """Look up every entry; `get(url) -> Page` defaults to fetch (tests pass a fake)."""
    get = get or fetch
    ids = [a for e in entries if e.url and (a := arxiv_id(e.url))]
    arx = arxiv(ids, get) if ids else {}

    def lookup(e: Entry, source: str) -> Record:
        url = e.url or ""
        if source == "arxiv":
            rec = arx[arxiv_id(url) or ""]
            if isinstance(rec, str):
                raise Unverified(rec)
            return rec
        if source == "crossref":
            return crossref(doi_of(url), get)
        if source == "page":
            return page_record(text_of(get(url)))
        return openlibrary(e, get)

    def one(e: Entry) -> Result:
        source = source_of(e)
        if source == "none":
            return Result(e, source)
        try:
            rec = lookup(e, source)
        except Unverified as x:
            return Result(e, source, unverified=str(x))
        except (ValueError, KeyError, ET.ParseError) as x:  # malformed response
            return Result(e, source, unverified=f"unreadable response: {x!r}")
        return Result(e, source, *compare(e, rec, source))

    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, entries))


# --- Report ------------------------------------------------------------------


def report(results: list[Result], notes: bool = False) -> str:
    lines = []
    for kind in ("mismatch", "unverified", "unlinked"):
        for r in results:
            if r.kind != kind:
                continue
            what = {
                "mismatch": " | ".join(r.problems),
                "unverified": r.unverified,
                "unlinked": "no link to check against",
            }[kind]
            src = "" if kind == "unlinked" else f" [{r.source}]"
            lines.append(f"{kind:<11} {r.entry.label}{src}: {what}")
    if notes:
        for r in results:
            if r.notes and r.kind in ("ok", "mismatch"):
                lines.append(
                    f"{'note':<11} {r.entry.label} [{r.source}]: {'; '.join(r.notes)}"
                )
    count = {
        k: sum(r.kind == k for r in results)
        for k in ("ok", "mismatch", "unverified", "unlinked")
    }
    n_notes = sum(bool(r.notes) for r in results if r.kind in ("ok", "mismatch"))
    summary = (
        f"{len(results)} entries: {count['ok']} match, {count['mismatch']} mismatch, "
        f"{count['unverified']} unverified, {count['unlinked']} unlinked"
    )
    if n_notes:
        summary += f"; {n_notes} with notes" + ("" if notes else " (--notes to show)")
    return "\n".join(lines + ([""] if lines else []) + [summary])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("file", help="Markdown file with the reading list")
    ap.add_argument(
        "--notes", action="store_true", help="also print accepted differences"
    )
    args = ap.parse_args(argv)
    with open(args.file, encoding="utf-8") as f:
        entries = parse_entries(f.read())
    if not entries:
        print("no entries found; see --help for the expected shape", file=sys.stderr)
        return 2
    results = check(entries)
    print(report(results, args.notes))
    return 1 if any(r.kind == "mismatch" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
