---
name: check-refs
user_invocable: true
description: Match references in a LaTeX paper or a Markdown reading list against academic databases, flag entries that need review, and suggest missing DOIs or URLs. Use when checking citations, a syllabus, or a reading list, or preparing a paper for submission.
---

# Check citation references

Match a LaTeX paper's references against academic databases with `bibsleuth`, or a Markdown reading list with the bundled `check_md_refs.py`. Database matches support bibliographic verification but do not prove that a citation supports the manuscript's claim.

## LaTeX papers

Use user-provided paths when available. Otherwise, identify the manuscript entrypoint from the project's build configuration and TeX structure, then resolve its bibliography declarations. Ask only if multiple plausible papers remain.

Run the database-only check by default:

```bash
uvx bibsleuth check <TEX_FILE> --bib <BIB_FILE> --no-llm
```

`uvx` supplies the tool without a separate installation step. Allow enough time for large bibliographies and use asynchronous execution when the harness supports it.

Read the generated `bibsleuth-report.md`. Prioritize unverified and error results, summarize suggested DOI or URL patches, and link the full report. Apply patches only when the user requested reference repair or approves the proposed edits, then check the diff for unintended bibliography changes.

For claim-level analysis, missing-citation suggestions, and contradiction searches, run the LLM mode only when the user requests it and accepts the additional model-provider exposure:

```bash
uvx bibsleuth check <TEX_FILE> --bib <BIB_FILE>
```

If the user explicitly requested full analysis at the outset, do not force a redundant `--no-llm` pass first. Present suspected miscitation and literature findings as items for review, not established errors.

## Markdown reading lists

For a syllabus or reading list in Markdown with entries like `- Author, Author and Author (Year). [Title](url). *Venue* Vol, pages.`, run the bundled checker:

```bash
uv run <skill-directory>/scripts/check_md_refs.py <FILE.md>
```

It looks up doi.org links in Crossref, arxiv.org links in the arXiv API, other links in the page's `citation_*` or Dublin Core meta tags, and unlinked italic titles (books) in OpenLibrary. It then compares title, authors (editors for `, eds.` entries), year, volume, and pages. `--help` lists the entry shapes it reads; other list items are skipped, so compare the entry count in the summary with the list.

The report lists only the entries that need a look:

- `mismatch`: the source disagrees with the entry. Open the source and fix the entry. Any mismatch makes the exit status 1.
- `unverified`: the source could not be read. Causes are a network or HTTP error, no record, a page without citation metadata, or a bot check (OpenReview, DBLP). Check these by hand or ask the user to; never try to get past a bot check.
- `unlinked`: no link and not a book, so there is nothing to compare. Suggest a link where one exists.

`--notes` adds the differences it accepts: a source that records only the first page (JSTOR), an arXiv version one year older than the conference version, a link to a later reprint, or a different subtitle. Summarize the counts, list every entry the report names, and edit the list only when the user asks.

Tests use canned responses and no network: `uv run --with pytest pytest skills/check-refs/scripts`.
