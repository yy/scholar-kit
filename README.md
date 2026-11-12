# scholar-kit

Academic research skills for AI agents: literature search, citation management, LaTeX checks, math verification, manuscript critique, and submission preparation.
Each skill is a directory containing `SKILL.md` and any supporting scripts, using the [Agent Skills format](https://agentskills.io/specification).
Use them with a harness that supports that format, or ask your agent to read a skill's instructions directly.
Capabilities depend on the harness and the tools available to it.
These skills support research checks and preparation; authors remain responsible for the work and their venue's AI use policy.

Previously named `claude-scholar`.

> **Note**: These skills are early-stage and not comprehensively tested. Proceed with caution!

> **Privacy**: Some skills send manuscript content, figures, or bibliographic data to AI model providers and external APIs. Read [PRIVACY.md](PRIVACY.md) to understand what data goes where, and discuss with your co-authors before using these skills on shared work.

## Basic principle for skill development and inclusion 

Don't overbuild. Foundation models and agent harnesses are getting better fast, and overbuilt structure becomes a maintenance burden. These skills exist where they add something an agent can't quickly or easily do on its own: calling external APIs, running programmatic verification, or encoding multi-step checks that are easy to forget. Keep instructions portable and harness-specific packaging small.

## Skills

### Lookup

Single-purpose data retrieval from external sources.

| Skill | Description |
|-------|-------------|
| `arxiv-metadata` | Fetch structured metadata for arXiv papers (title, authors, date, categories, DOI) |
| `doi-bibtex` | Fetch BibTeX entries from DOIs and add to `.bib` files |
| `openalex` | Query and analyze scholarly works via the [OpenAlex](https://openalex.org) API |

### Analysis

Inspect, verify, or critique artifacts — from programmatic checks to structured review.

| Skill | Description |
|-------|-------------|
| `check-refs` | Verify citation references in LaTeX papers against academic databases using [bibsleuth](https://github.com/yy/bibsleuth) |
| `latex-cleanup` | Review LaTeX documents for common issues, style consistency, typography, cross-references, and draft artifacts |
| `verify-math` | Verify mathematical derivations step-by-step using SymPy |
| `critique-figures` | Critique figures for format, colorblind safety, legibility, overplotting, and chart choice |
| `critique-manuscript` | Structured self-review of your manuscript |

### Workflow

Multi-step workflows — coordinating checks, preparing submissions, or transforming documents.

| Skill | Description |
|-------|-------------|
| `presubmit-checks` | Pre-submission checklist — references, LaTeX cleanup, build, figure format, and front matter |
| `bib-doi-toggle` | Toggle DOI/URL display in a biblatex bibliography so refs are clickable in the compiled PDF |
| `arxiv-prep` | Prepare an arXiv submission package — clean source, compile, extract metadata, create tarball |
| `anonymize-repo` | Export an anonymized, history-free copy of a research repo for double-blind review, scanned for anything identifying |
| `accessible-pdf` | Make LaTeX documents produce accessible tagged PDFs (PDF/UA-1, PDF/A-2b) — non-destructive copy + LuaLaTeX build |

## Skill details

### Lookup

#### `arxiv-metadata`

Fetches structured metadata from arXiv given an arXiv ID or URL — title, authors, date, categories, DOI. Handles multiple ID formats (new and old-style). Mainly used by other skills and agents rather than invoked directly.

#### `doi-bibtex`

Given a DOI (bare or full URL), fetches the BibTeX entry and appends it to the project's `.bib` file, checking for duplicates. Mainly used when adding references.

#### `openalex`

Queries the [OpenAlex](https://openalex.org) API for literature searches, citation analysis, and bibliometric queries. The helper script handles authentication, rate limiting, retries, and pagination. Set `OPENALEX_API_KEY` for normal use; limited no-key requests are supported.

### Analysis

#### `check-refs`

Verifies that every citation in a LaTeX paper exists in academic databases, flags suspicious entries, and suggests missing DOIs/URLs. Uses [bibsleuth](https://github.com/yy/bibsleuth). Can run standalone or as part of `presubmit-checks`.

#### `latex-cleanup`

Systematic review of LaTeX documents for common issues: style consistency, typography, cross-references, draft artifacts, debugging code. Identifies all `.tex` files in the project (including `\input`/`\include` targets) and flags problems.

#### `verify-math`

Step-by-step verification of mathematical derivations using SymPy. Each derivation step is validated programmatically — useful for catching algebra and calculus errors in proofs and equations.

#### `critique-figures`

Critiques academic figures using a mix of programmatic checks and vision analysis. Checks file format and resolution (flags raster exports of plots/charts, detects bitmap-in-PDF wrappers), colorblind risk (red-green reliance), excessive categories, font legibility, overplotted scatterplots, and dynamite plots. Handles multi-panel figures by evaluating each panel individually. Resolves figures from `\includegraphics` references in the TeX tree. Report-only — does not modify figures.

#### `critique-manuscript`

Reviews an authorized manuscript against seven criteria: literature and novelty, methodological rigor, causal claims, data quality, generalizability, mechanism, and clarity.
Returns findings in chat, anchored to the manuscript, with risks, strengths, and anticipated reviewer questions.
External literature search is opt-in.

### Orchestration

#### `presubmit-checks`

Pre-submission checklist that orchestrates multiple checks in parallel: references (`check-refs`), LaTeX cleanup (`latex-cleanup`), build verification, figure format checks (flags bitmap figures that should be vector), and front matter review (affiliations, acknowledgements, data availability). Presents a unified report organized by severity.

#### `bib-doi-toggle`

Toggles DOI/URL display in the compiled bibliography of a biblatex paper by editing the `\usepackage[...]{biblatex}` options. Turning on makes references clickable in the PDF so co-authors and reviewers can verify each citation. Invoking with `on` / `off` applies directly; with no argument the skill asks. After turning on, it scans the `.bib` for entries missing a DOI and offers to backfill them via `doi-bibtex` or `openalex`. biblatex-only (bails out for natbib).

#### `arxiv-prep`

Automates arXiv submission packaging: optimizes bibliography (removes unused entries, reorders to match appearance), cleans source files, removes cruft, verifies compilation, extracts metadata for the submission form, and produces a ready-to-upload tarball. Uses [bib_optimizer](https://github.com/kwyip/bib_optimizer) and Google's [arxiv-latex-cleaner](https://github.com/google-research/arxiv-latex-cleaner) under the hood. Complements `presubmit-checks` (content quality) by handling the packaging side.

#### `anonymize-repo`

Exports the tracked files of a commit, with no git history, for a code-and-data link under double-blind review. A per-repo `anonymize.toml` lists the paths to leave out, the text to replace (each rule must match), and the identity patterns; the bundled script blanks Office and PDF author metadata, masks names left in PDF strings such as annotation authors, and fails if any pattern survives in text, Office parts, binary files, or PDF text. The skill then checks that the export still builds and tests, and covers publishing through Anonymous GitHub or an anonymized OSF view-only link. `--push` commits each new export as "Anonymous" on top of the mirror's head and pushes without force, so re-publishing after a change is one command.

#### `accessible-pdf`

Transforms a LaTeX document to produce tagged, screen-reader-friendly PDFs compliant with PDF/UA-1 and PDF/A-2b. Creates a non-destructive copy of the main `.tex` file with an `-accessible` suffix, applies five categories of changes (document metadata, OpenType fonts, hyperref accessibility metadata, symbol font replacements, LuaLaTeX build), and adds a `make accessible` target. Verifies tagging with `pdfinfo`. Works with any LaTeX document — CVs, syllabi, reports, papers. Requires TeX Live 2025+.

## Installation

### Copy or symlink skills

Clone the repository, then copy or symlink the individual skill directories you want into your harness's documented skills directory.
Keep each whole directory so its scripts and other resources remain available.

```sh
git clone https://github.com/yy/scholar-kit.git

# Replace this example path with your harness's skills directory.
skill_target="/path/to/agent/skills"
mkdir -p "$skill_target"
ln -s "$PWD/scholar-kit/skills/check-refs" "$skill_target/check-refs"
```

Use `cp -R` instead of `ln -s` if your harness does not follow symlinks.
Install companion skills when a workflow needs them: `presubmit-checks` uses `check-refs`, `latex-cleanup`, and `critique-figures`.
Ask the agent to use a skill by name, using your harness's invocation syntax.

### Optional: Skillink

[Skillink](https://github.com/yy/skillink) manages skill symlinks through a TOML manifest, with a selection and destination for each agent profile.
It is useful when several harnesses share the same source checkout.
For example, save this as `skills.toml` and replace the paths with your own:

```toml
format_version = 1

[sources]
scholar = "~/git/scholar-kit/skills"

[profiles.my-agent]
target = "~/path/to/agent/skills"
include = ["scholar/check-refs", "scholar/critique-manuscript"]
# Use ["scholar/*"] to include all skills.
```

From a Skillink checkout:

```sh
uv run bin/install_skills.py my-agent --manifest /path/to/skills.toml --dry-run
uv run bin/install_skills.py my-agent --manifest /path/to/skills.toml
uv run bin/install_skills.py my-agent --manifest /path/to/skills.toml --check
```

Skillink is optional; scholar-kit has no custom installer or background service.

### Claude Code plugin

The existing marketplace remains available as a small adapter:

```text
/plugin marketplace add yy/scholar-kit
/plugin install scholar-kit@scholar-kit
/reload-plugins
```

Plugin skills use names such as `/scholar-kit:check-refs`.
See the [Claude Code marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces) for installation details.
Use one installation method per harness to avoid loading duplicate skills.
If you used the old plugin, uninstall it and remove the old marketplace before installing under the new name.

### Existing checkout

Update its remote after the rename:

```sh
git remote set-url origin https://github.com/yy/scholar-kit.git
```

If you also rename the local checkout directory, update any skill symlinks or Skillink source paths that pointed to it.

## Dependencies

Some skills require external tools:

- **check-refs**: [bibsleuth](https://github.com/yy/bibsleuth) (`uvx bibsleuth`)
- **verify-math**: [SymPy](https://www.sympy.org) (`uv run` with sympy available)
- **arxiv-prep**: [arxiv-latex-cleaner](https://github.com/google-research/arxiv-latex-cleaner) (`uvx arxiv-latex-cleaner`), [bib_optimizer](https://github.com/kwyip/bib_optimizer) (`uvx bib_optimizer`), optionally [bibtool](https://github.com/ge-ne/bibtool) for merging shared bibliographies
- **openalex**: Python with `requests` and a free OpenAlex API key for normal use (included in the plugin's helper scripts)
- **arxiv-metadata**: Python with `requests` (`uv run` with inline script dependencies)
- **accessible-pdf**: TeX Live 2025+ with LuaLaTeX, `latexmk`, `pdfinfo` (poppler)

## Acknowledgements

- [K-Dense-AI/claude-scientific-skills](https://github.com/K-Dense-AI/claude-scientific-skills) — inspiration and starting point for the OpenAlex skill
- [Sadamori Kojaku](https://github.com/skojaku) — inspiration for the math verification skill
- [Trevor Campbell's arXiv checklist](https://trevorcampbell.me/html/arxiv.html) — inspiration for the arXiv prep skill
- [NERDS@ITU arxiv submission guide](https://github.com/NERDSITU/research-resources/blob/main/arxivsubmission.md) — bibliography optimization workflow for arXiv prep

## License

MIT
