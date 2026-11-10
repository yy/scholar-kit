"""Run: uv run --with pytest pytest skills/check-refs/scripts

Canned responses are trimmed from real Crossref, arXiv, OpenLibrary and publisher
responses (November 2026). No test touches the network.
"""

import io
import json
import urllib.error
from email.message import Message

import check_md_refs as cmr
import pytest
from check_md_refs import Page, Unverified


def md(*lines: str) -> str:
    return "\n".join(lines)


def entry(line: str) -> cmr.Entry:
    (e,) = cmr.parse_entries(line)
    return e


class fake_get:
    """get(url) for check(): the first key found in the URL picks the response."""

    def __init__(self, responses: dict[str, Page | str]):
        self.responses, self.calls = responses, []

    def __call__(self, url: str) -> Page:
        self.calls.append(url)
        for key, resp in self.responses.items():
            if key in url:
                return resp if isinstance(resp, Page) else Page(url, 200, resp)
        raise AssertionError(f"unexpected request: {url}")


def crossref_json(**msg) -> str:
    return json.dumps({"status": "ok", "message": msg})


# --- Parsing -----------------------------------------------------------------


def test_parse_linked_article():
    e = entry(
        "- Radovanović, Nanopoulos and Ivanović (2010). [Hubs in space: Popular nearest"
        " neighbors in high-dimensional data](https://jmlr.org/papers/v11/radovanovic10a"
        ".html). *Journal of Machine Learning Research* 11, 2487–2531."
    )
    assert (e.year, e.url, e.book) == (
        2010,
        "https://jmlr.org/papers/v11/radovanovic10a.html",
        False,
    )
    assert e.title.startswith("Hubs in space")
    assert (e.venue, e.vol, e.pages) == (
        "Journal of Machine Learning Research",
        "11",
        "2487–2531",
    )


def test_parse_issue_reprint_book_unlinked_and_non_entries():
    text = md(
        "# Readings",
        "Some prose (2020). Not a list item.",
        "- Hegselmann and Krause (2002). [Opinion dynamics](https://www.jasss.org/5/3/2.html)."
        " *Journal of Artificial Societies and Social Simulation* 5(3), 2.",
        "- Converse (1964). [The nature of belief systems in mass publics]"
        "(https://doi.org/10.1080/08913810608443650). In D. E. Apter (Ed.), *Ideology and"
        " Discontent*. Free Press. Reprinted in *Critical Review* 18, 1–74 (2006).",
        "- Lewis (1969). *Convention: A Philosophical Study*. Harvard University Press.",
        "- Gandhi et al. (2026). Evaluating belief dynamics models. Submitted.",
        "- A bullet without a year.",
    )
    hk, conv, lewis, gandhi = cmr.parse_entries(text)
    assert (hk.line, hk.vol, hk.pages) == (3, "5", "2")
    assert (conv.venue, conv.vol, conv.pages, conv.reprint) == (
        "Critical Review",
        "18",
        "1–74",
        2006,
    )
    assert (lewis.book, lewis.title, lewis.url) == (
        True,
        "Convention: A Philosophical Study",
        None,
    )
    assert (gandhi.book, gandhi.url, gandhi.title) == (
        False,
        None,
        "Evaluating belief dynamics models",
    )


def test_named_authors():
    assert cmr.named_authors("Park, Choe and Veitch") == (
        ["Park", "Choe", "Veitch"],
        False,
        False,
    )
    assert cmr.named_authors("Burns et al.") == (["Burns"], True, False)
    assert cmr.named_authors("Lehmann and Ahn, eds.") == (
        ["Lehmann", "Ahn"],
        False,
        True,
    )
    assert cmr.named_authors("A, B, and C") == (["A", "B", "C"], False, False)


@pytest.mark.parametrize(
    "raw, fam",
    [
        ("Miloš Radovanović", "Radovanović"),
        ("Hegselmann, Rainer", "Hegselmann"),
        ("Robert Boyd - undifferentiated", "Boyd"),  # OpenLibrary
        ("Boyd, Robert Ph. D.", "Boyd"),
        ("David J.C. MacKay", "MacKay"),
        ("Luigi Luca Cavalli-Sforza", "Cavalli-Sforza"),
        ("Martin Luther King Jr.", "King"),
    ],
)
def test_family(raw, fam):
    assert cmr.family(raw) == fam


def test_same_name():
    assert cmr.same_name("Radovanović", "Radovanovic")
    assert cmr.same_name("van der Does", "Does")
    assert cmr.same_name("Barthélemy", "Barthelemy")
    assert not cmr.same_name("Smith", "Rosenthal")


# --- Crossref ----------------------------------------------------------------

GALESIC = crossref_json(
    title=[
        "Integrating social and cognitive aspects of belief dynamics: towards a unifying framework"
    ],
    author=[
        {"family": f} for f in ["Galesic", "Olsson", "Dalege", "van der Does", "Stein"]
    ],
    issued={"date-parts": [[2021, 3]]},
    volume="18",
    **{"article-number": "rsif.2020.0857"},
)
HERRMANN = crossref_json(  # online first 2024, in the 2025 issue
    title=["Standards for Belief Representations in LLMs"],
    author=[{"family": "Herrmann"}, {"family": "Levinstein"}],
    issued={"date-parts": [[2024, 12, 7]]},
    **{
        "published-online": {"date-parts": [[2024, 12, 7]]},
        "journal-issue": {
            "issue": "1",
            "published-online": {"date-parts": [[2025, 3]]},
        },
        "article-number": "5",
    },
    volume="35",
)
LEHMANN = crossref_json(
    type="book",
    title=["Complex Spreading Phenomena in Social Systems"],
    editor=[
        {"family": "Lehmann", "given": "Sune"},
        {"family": "Ahn", "given": "Yong-Yeol"},
    ],
    issued={"date-parts": [[2018]]},
)
SWIDLER = crossref_json(  # JSTOR deposits only the first page
    title=["Culture in Action: Symbols and Strategies"],
    author=[{"family": "Swidler"}],
    issued={"date-parts": [[1986, 4]]},
    volume="51",
    page="273",
)
CONVERSE = crossref_json(
    title=["The nature of belief systems in mass publics (1964)"],
    author=[{"family": "Converse"}],
    issued={"date-parts": [[2006, 1]]},
    volume="18",
    page="1-74",
)

CROSSREF_LIST = md(
    "- Galesic et al. (2021). [Integrating social and cognitive aspects of belief dynamics:"
    " towards a unifying framework](https://doi.org/10.1098/rsif.2020.0857). *Journal of the"
    " Royal Society Interface* 18, 20200857.",
    "- Herrmann and Levinstein (2025). [Standards for belief representations in LLMs]"
    "(https://doi.org/10.1007/s11023-024-09709-6). *Minds and Machines* 35, 5.",
    "- Lehmann and Ahn, eds. (2018). [Complex Spreading Phenomena in Social Systems]"
    "(https://doi.org/10.1007/978-3-319-77332-2). Springer.",
    "- Swidler (1986). [Culture in action: Symbols and strategies](https://doi.org/10.2307/2095521)."
    " *American Sociological Review* 51, 273–286.",
    "- Converse (1964). [The nature of belief systems in mass publics]"
    "(https://doi.org/10.1080/08913810608443650). In D. E. Apter (Ed.), *Ideology and"
    " Discontent*. Free Press. Reprinted in *Critical Review* 18, 1–74 (2006).",
)
CROSSREF_GET = {
    "10.1098/rsif.2020.0857": GALESIC,
    "10.1007/s11023-024-09709-6": HERRMANN,
    "10.1007/978-3-319-77332-2": LEHMANN,
    "10.2307/2095521": SWIDLER,
    "10.1080/08913810608443650": CONVERSE,
}


def test_crossref_known_differences_pass():
    galesic, herrmann, lehmann, swidler, converse = cmr.check(
        cmr.parse_entries(CROSSREF_LIST), fake_get(CROSSREF_GET)
    )
    for r in (galesic, herrmann, lehmann, swidler, converse):
        assert (r.source, r.kind, r.problems) == ("crossref", "ok", [])
    assert galesic.notes == herrmann.notes == lehmann.notes == []
    assert swidler.notes == ["source records only the first page (273)"]
    assert converse.notes == ["link is to the 2006 reprint of the 1964 original"]


def test_crossref_record_fields():
    rec = cmr.crossref_record(
        json.loads(HERRMANN)["message"], "10.1007/s11023-024-09709-6"
    )
    assert rec.years == {2024, 2025}
    assert (rec.vol, rec.pages, rec.article_id) == ("35", "5", False)
    rec = cmr.crossref_record(json.loads(GALESIC)["message"], "10.1098/RSIF.2020.0857")
    assert rec.article_id


def test_crossref_real_errors_are_mismatches():
    wrong = CROSSREF_LIST.replace("(2021)", "(2019)")
    wrong = wrong.replace("Herrmann and Levinstein", "Herrmann and Smith")
    wrong = wrong.replace("Review* 51,", "Review* 52,")
    wrong = wrong.replace("Lehmann and Ahn, eds.", "Lehmann and Ahn")
    galesic, herrmann, lehmann, swidler, _ = cmr.check(
        cmr.parse_entries(wrong), fake_get(CROSSREF_GET)
    )
    assert galesic.problems == ["year 2019 vs source [2021]"]
    assert herrmann.problems == ["author 'Smith' vs source 'Levinstein'"]
    assert lehmann.problems == [
        "source lists editors, not authors: ['Lehmann', 'Ahn'] (add ', eds.')"
    ]
    assert swidler.problems == ["volume 52 vs source 51"]


def test_crossref_404_is_unverified():
    get = fake_get(
        {
            "api.crossref.org": Page(
                "https://api.crossref.org/x", 404, "Resource not found."
            )
        }
    )
    (r,) = cmr.check(cmr.parse_entries(CROSSREF_LIST.splitlines()[0]), get)
    assert (r.kind, r.unverified) == (
        "unverified",
        "no Crossref record for 10.1098/rsif.2020.0857",
    )


# --- Publisher pages ---------------------------------------------------------

JMLR = """<html><head>
<meta http-equiv="Content-type" content="text/html;charset=UTF-8">
<meta name="citation_title" content="Hubs in Space: Popular Nearest Neighbors in High-Dimensional Data">
<meta name="citation_author" content="Milo&scaron; Radovanovi&amp;#263;">
<meta name="citation_author" content="Alexandros Nanopoulos">
<meta name="citation_author" content="Mirjana Ivanovi&amp;#263;">
<meta name="citation_journal_title" content="Journal of Machine Learning Research">
<meta name="citation_volume" content="11">
<meta name="citation_firstpage" content="2487">
<meta name="citation_lastpage" content="2531">
<meta name="citation_publication_date" content="2010">
</head></html>"""

JASSS = """<html><head>
<meta NAME="DC.Title" CONTENT="
Opinion dynamics and bounded confidence models, analysis and simulation
">
<meta NAME="DC.Creator" CONTENT="
Hegselmann, Rainer; Krause, Ulrich
">
<meta NAME="DC.Date" CONTENT="
2002-6-30
">
<title>Rainer Hegselmann and Ulrich Krause: Opinion Dynamics</title></head></html>"""

OPENREVIEW_CHALLENGE = (
    "<html><head><title>Verifying your browser | OpenReview</title></head></html>"
)
DBLP_ANUBIS = "<!doctype html><html><head><title>Making sure you&#39;re not a bot!</title></head></html>"
CLOUDFLARE = "<html><head><title>Just a moment...</title></head></html>"

RADOVANOVIC = (
    "- Radovanović, Nanopoulos and Ivanović (2010). [Hubs in space: Popular nearest neighbors"
    " in high-dimensional data](https://jmlr.org/papers/v11/radovanovic10a.html). *Journal of"
    " Machine Learning Research* 11, 2487–2531."
)
HEGSELMANN = (
    "- Hegselmann and Krause (2002). [Opinion dynamics and bounded confidence: Models, analysis"
    " and simulation](https://www.jasss.org/5/3/2.html). *Journal of Artificial Societies and"
    " Social Simulation* 5(3), 2."
)
HASE = (
    "- Hase et al. (2024). [Fundamental problems with model editing](https://openreview.net/"
    "forum?id=LRf19n5Ly3) *Transactions on Machine Learning Research*."
)


def test_page_meta_double_escaped_entities_and_page_range():
    rec = cmr.page_record(JMLR)
    assert rec.authors == ["Radovanović", "Nanopoulos", "Ivanović"]
    assert (rec.vol, rec.pages, rec.years) == ("11", "2487-2531", {2010})
    (r,) = cmr.check(cmr.parse_entries(RADOVANOVIC), fake_get({"jmlr.org": JMLR}))
    assert (r.kind, r.problems, r.notes) == ("ok", [], [])


def test_page_meta_dublin_core_with_semicolon_authors():
    rec = cmr.page_record(JASSS)
    assert (rec.authors, rec.years) == (["Hegselmann", "Krause"], {2002})
    (r,) = cmr.check(cmr.parse_entries(HEGSELMANN), fake_get({"jasss.org": JASSS}))
    assert (r.kind, r.problems) == ("ok", [])


def test_page_mismatch_still_reported():
    wrong = RADOVANOVIC.replace(
        "Hubs in space: Popular nearest neighbors", "Rare far neighbors"
    )
    (r,) = cmr.check(cmr.parse_entries(wrong), fake_get({"jmlr.org": JMLR}))
    assert r.kind == "mismatch" and r.problems[0].startswith("title 0.")


@pytest.mark.parametrize(
    "page, host",
    [
        (
            Page(
                "https://openreview.net/challenge?redirect=%2Fforum",
                200,
                OPENREVIEW_CHALLENGE,
            ),
            "openreview.net",
        ),
        (
            Page(
                "https://openreview.net/forum?id=LRf19n5Ly3", 200, OPENREVIEW_CHALLENGE
            ),
            "openreview.net",
        ),
        (
            Page("https://dblp.org/rec/conf/nips/LevyG14.html", 202, DBLP_ANUBIS),
            "dblp.org",
        ),
        (Page("https://example.org/paper", 403, CLOUDFLARE), "example.org"),
    ],
)
def test_bot_checks_are_unverified_not_mismatches(page, host):
    (r,) = cmr.check(cmr.parse_entries(HASE), fake_get({"openreview.net": page}))
    assert r.kind == "unverified"
    assert r.unverified == f"could not fetch: bot check at {host} (check by hand)"


def test_http_error_and_missing_metadata_are_unverified():
    (r,) = cmr.check(
        cmr.parse_entries(HASE),
        fake_get({"openreview": Page("https://openreview.net/x", 500, "")}),
    )
    assert r.unverified == "could not fetch: HTTP 500 from https://openreview.net/x"
    (r,) = cmr.check(
        cmr.parse_entries(HASE),
        fake_get({"openreview": "<html><title>Forum</title></html>"}),
    )
    assert r.unverified == "page has no citation metadata"


def test_network_failure_is_unverified():
    def get(url):
        raise Unverified(f"could not fetch {url}: timed out")

    (r,) = cmr.check(cmr.parse_entries(RADOVANOVIC), get)
    assert r.kind == "unverified" and r.unverified.endswith("timed out")


# --- arXiv -------------------------------------------------------------------

ATOM = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2212.03827v2</id>
    <title>Discovering Latent Knowledge in Language Models
      Without Supervision</title>
    <published>2022-12-07T18:17:56Z</published>
    <author><name>Collin Burns</name></author>
    <author><name>Haotian Ye</name></author>
    <author><name>Dan Klein</name></author>
    <author><name>Jacob Steinhardt</name></author>
  </entry>
</feed>"""

BURNS = (
    "- Burns et al. (2023). [Discovering latent knowledge in language models without supervision]"
    "(https://arxiv.org/abs/2212.03827). *ICLR*."
)


def test_arxiv_year_before_venue_is_a_note():
    get = fake_get({"export.arxiv.org": ATOM})
    (r,) = cmr.check(cmr.parse_entries(BURNS), get)
    assert (r.source, r.kind, r.notes) == (
        "arxiv",
        "ok",
        ["arXiv version is from 2022, ICLR 2023"],
    )
    assert get.calls == [
        "https://export.arxiv.org/api/query?max_results=100&id_list=2212.03827"
    ]


@pytest.mark.parametrize(
    "line, problem",
    [
        (BURNS.replace("*ICLR*", "*arXiv*"), "year 2023 vs source [2022]"),
        (BURNS.replace("(2023)", "(2024)"), "year 2024 vs source [2022]"),
        (
            BURNS.replace("Burns et al.", "Burns and Ye"),
            "2 authors named vs 4 in source: ['Burns', 'Ye', 'Klein', 'Steinhardt']",
        ),
    ],
)
def test_arxiv_real_errors(line, problem):
    (r,) = cmr.check(cmr.parse_entries(line), fake_get({"export.arxiv.org": ATOM}))
    assert r.problems == [problem]


def test_arxiv_ids_from_urls():
    assert cmr.arxiv_id("https://arxiv.org/abs/2212.03827v3") == "2212.03827"
    assert cmr.arxiv_id("https://arxiv.org/pdf/2212.03827.pdf") == "2212.03827"
    assert cmr.arxiv_id("https://arxiv.org/abs/cond-mat/0106096") == "cond-mat/0106096"
    assert cmr.arxiv_id("https://doi.org/10.48550/arXiv.2212.03827") == "2212.03827"
    assert cmr.arxiv_id("https://doi.org/10.1098/rsif.2020.0857") is None


def test_arxiv_missing_id_and_failed_call():
    out = cmr.arxiv(["2212.03827", "9999.99999"], fake_get({"export.arxiv.org": ATOM}))
    assert out["9999.99999"] == "no arXiv record for 9999.99999"
    out = cmr.arxiv(["2212.03827"], fake_get({"export.arxiv.org": Page("u", 503, "")}))
    assert out["2212.03827"] == "could not fetch: HTTP 503 from u"


# --- OpenLibrary -------------------------------------------------------------


def ol(*docs) -> str:
    return json.dumps({"numFound": len(docs), "docs": list(docs)})


LEWIS_DOCS = ol(  # the first hit is a later edition; one is another Lewis's book
    {
        "title": "Convention",
        "author_name": ["David Lewis"],
        "first_publish_year": 2008,
        "publish_year": [2008, 2013],
    },
    {
        "title": "Catalogue of the Syriac MSS. in the Convent of S. Catharine",
        "author_name": ["Agnes Smith Lewis"],
        "first_publish_year": 2012,
    },
    {
        "title": "Convention",
        "author_name": ["David Lewis"],
        "first_publish_year": 1969,
        "publish_year": [1969, 1986],
    },
)
BOYD_DOCS = ol(  # both authors on the 1988 record, the 1985 one lists only Boyd
    {
        "title": "Culture and the Evolutionary Process",
        "author_name": ["Robert Boyd - undifferentiated", "Peter J. Richerson"],
        "first_publish_year": 1988,
        "publish_year": [1988],
    },
    {
        "title": "Culture and the evolutionary process",
        "author_name": ["Boyd, Robert Ph. D."],
        "first_publish_year": 1985,
        "publish_year": [1985],
    },
)
SETHNA_DOCS = ol(
    {
        "title": "Statistical Mechanics",
        "author_name": ["James P. Sethna"],
        "first_publish_year": 2006,
        "publish_year": [2006, 2010],
    },
    {
        "title": "Statistical Mechanics : Entropy, Order Parameters, and Complexity",
        "author_name": ["James Sethna"],
        "first_publish_year": 2021,
    },
)
MACKAY_DOCS = ol(
    {
        "title": "Information Theory, Inference & Learning Algorithms",
        "author_name": ["David J.C. MacKay"],
        "first_publish_year": 2003,
        "publish_year": [2003, 2004],
    },
)
BOOKS = md(
    "- Lewis (1969). *Convention: A Philosophical Study*. Harvard University Press.",
    "- Boyd and Richerson (1985). *Culture and the Evolutionary Process*. University of Chicago Press.",
    "- Sethna (2006). *Statistical Mechanics: Entropy, Order Parameters, and Complexity*. Oxford University Press.",
    "- MacKay (2003). *Information Theory, Inference, and Learning Algorithms*. Cambridge University Press.",
)


def books_get(**extra):
    return fake_get(
        {
            "title=Convention": LEWIS_DOCS,
            "title=Culture+and": BOYD_DOCS,
            "title=Statistical+Mechanics": SETHNA_DOCS,
            "title=Information+Theory": ol(),  # field search misses "&" vs "and"
            "q=Information+Theory": MACKAY_DOCS,
            **extra,
        }
    )


def test_books_by_main_title_and_any_edition_year():
    get = books_get()
    results = cmr.check(cmr.parse_entries(BOOKS), get)
    for r in results:
        assert (r.source, r.kind, r.problems, r.notes) == ("openlibrary", "ok", [], [])
    lewis_query = next(u for u in get.calls if "Convention" in u)
    assert (
        "title=Convention&author=Lewis" in lewis_query
    )  # main title, before the colon


def test_book_wrong_year_and_missing_book():
    wrong = BOOKS.replace("Lewis (1969)", "Lewis (1959)")
    lewis = cmr.check(cmr.parse_entries(wrong), books_get())[0]
    assert lewis.problems == ["year 1959 vs source [1969, 1986, 2008, 2013]"]
    line = "- Nobody (2000). *An Unknown Book*. Nowhere Press."
    (r,) = cmr.check(cmr.parse_entries(line), fake_get({"openlibrary.org": ol()}))
    assert (r.kind, r.unverified) == ("unverified", "no matching book in OpenLibrary")


# --- Comparison details --------------------------------------------------------


def test_compare_et_al_and_subtitles():
    rec = cmr.Record(
        title="Opinion dynamics: a survey",
        authors=["Hegselmann", "Krause"],
        years={2002},
    )
    e = entry(
        "- Hegselmann et al. (2002). [Opinion dynamics: Models](https://x.org). *J*."
    )
    problems, notes = cmr.compare(e, rec, "page")
    assert problems == ["'et al.' but source has 2 authors: ['Hegselmann', 'Krause']"]
    assert notes == ["subtitle differs; source: 'Opinion dynamics: a survey'"]


def test_compare_pages():
    rec = cmr.Record(title="T", authors=["A"], years={2000}, vol="5", pages="10-20")
    for pages, ok in [
        ("10–20", True),
        ("10", True),
        ("11–20", False),
        ("10–21", False),
    ]:
        e = entry(f"- A (2000). [T](https://x.org). *J* 5, {pages}.")
        assert (cmr.compare(e, rec, "page")[0] == []) is ok, pages


# --- Report, CLI, fetch --------------------------------------------------------


def test_report_and_exit_status(tmp_path, monkeypatch, capsys):
    path = tmp_path / "list.md"
    path.write_text(
        md(
            CROSSREF_LIST.splitlines()[3],
            BURNS,
            HASE,
            "- Gandhi et al. (2026). A paper. Submitted.",
        ),
        encoding="utf-8",
    )
    get = fake_get(
        {
            "crossref": SWIDLER,
            "export.arxiv.org": ATOM,
            "openreview.net": Page(
                "https://openreview.net/challenge", 200, OPENREVIEW_CHALLENGE
            ),
        }
    )
    monkeypatch.setattr(cmr, "fetch", get)
    assert cmr.main([str(path)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "unverified  L3 Hase et al. (2024) [page]: could not fetch: bot check at openreview.net (check by hand)",
        "unlinked    L4 Gandhi et al. (2026): no link to check against",
        "",
        "4 entries: 2 match, 0 mismatch, 1 unverified, 1 unlinked; 2 with notes (--notes to show)",
    ]
    assert cmr.main([str(path), "--notes"]) == 0
    assert (
        "note        L2 Burns et al. (2023) [arxiv]: arXiv version is from 2022, ICLR 2023"
        in capsys.readouterr().out
    )

    path.write_text(BURNS.replace("(2023)", "(2021)"), encoding="utf-8")
    assert cmr.main([str(path)]) == 1


class FakeResponse(io.BytesIO):
    def __init__(self, url, body, status=200):
        super().__init__(body.encode())
        self.url, self.status, self.headers = url, status, Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(url, code, body, headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(url, code, "error", msg, io.BytesIO(body.encode()))


def test_fetch_never_retries_a_bot_check(monkeypatch):
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        raise http_error(req.full_url, 503, CLOUDFLARE)

    monkeypatch.setattr(cmr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cmr.time, "sleep", lambda s: pytest.fail("slept"))
    page = cmr.fetch("https://example.org/paper")
    assert (page.status, len(calls)) == (503, 1)
    with pytest.raises(Unverified, match="bot check at example.org"):
        cmr.text_of(page)


def test_fetch_retries_rate_limits_with_retry_after(monkeypatch):
    replies = [
        http_error("u", 429, "slow down", {"Retry-After": "7"}),
        FakeResponse("u", "ok"),
    ]
    slept = []

    def urlopen(req, timeout):
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(cmr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cmr.time, "sleep", slept.append)
    assert cmr.fetch("https://api.crossref.org/works/x") == Page("u", 200, "ok")
    assert slept == [7]


def test_fetch_network_failure_raises_unverified(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(cmr.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(cmr.time, "sleep", lambda s: None)
    with pytest.raises(
        Unverified, match="could not fetch https://proceedings.neurips.cc/x: timed out"
    ):
        cmr.fetch("https://proceedings.neurips.cc/x")
