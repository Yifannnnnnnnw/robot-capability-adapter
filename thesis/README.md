# UCL MSc thesis

This directory is the repository's only maintained MSc thesis source tree.
Keep Overleaf exports and local edits here rather than adding thesis files to
the repository root or creating another thesis directory.

- Main document: `Main.tex`
- Bibliography: `example.bib`
- Writing and review standard: `ACADEMIC_WRITING_GUIDE.md`
- Template licensing: `TEMPLATE_LICENSES.md`

## Build

The intended engine is LuaLaTeX. If `latexmk` is installed, run:

```bash
cd thesis
latexmk Main.tex
```

Without `latexmk`, use the equivalent sequence:

```bash
cd thesis
lualatex -interaction=nonstopmode -halt-on-error Main.tex
bibtex Main
lualatex -interaction=nonstopmode -halt-on-error Main.tex
lualatex -interaction=nonstopmode -halt-on-error Main.tex
```

The class also supports pdfLaTeX as a fallback; replace `lualatex` with
`pdflatex` in the manual sequence if the LuaTeX font loader is unavailable.

Generated PDFs and auxiliary files are local build outputs and are ignored by
Git. In Overleaf, set `Main.tex` as the main document and select LuaLaTeX.

The class can use locally installed Cambria fonts, but proprietary font files
are not tracked. It falls back to Caladea or TeX Gyre Termes when Cambria is
unavailable.

## Publish to Overleaf

The local `thesis/` directory is the source of truth. The Overleaf project
stores the contents of this directory at its repository root and has a Git
history separate from the monorepo. Do not push the monorepo `main` branch
directly to Overleaf.

Commit the intended thesis changes locally first. Then, from the monorepo root,
use a temporary clone to preserve the Overleaf history while replacing its
tracked thesis snapshot with the committed local snapshot:

```bash
OVERLEAF_SYNC_DIR="$(mktemp -d /tmp/autoadapter-overleaf-sync.XXXXXX)"
OVERLEAF_EXPORT_DIR="$(mktemp -d /tmp/autoadapter-overleaf-export.XXXXXX)"

git clone --branch main --single-branch \
  "$(git remote get-url overleaf)" "$OVERLEAF_SYNC_DIR"
git archive --format=tar HEAD:thesis \
  | tar -xf - -C "$OVERLEAF_EXPORT_DIR"
rsync -a --delete --exclude='.git/' \
  "$OVERLEAF_EXPORT_DIR/" "$OVERLEAF_SYNC_DIR/"

git -C "$OVERLEAF_SYNC_DIR" status --short
git -C "$OVERLEAF_SYNC_DIR" diff --check
git -C "$OVERLEAF_SYNC_DIR" add -A
git -C "$OVERLEAF_SYNC_DIR" diff --cached --stat
git -C "$OVERLEAF_SYNC_DIR" diff --cached --check
git -C "$OVERLEAF_SYNC_DIR" commit \
  -m "Sync thesis revisions from local repository"
git -C "$OVERLEAF_SYNC_DIR" push origin main
git -C "$OVERLEAF_SYNC_DIR" ls-remote --heads origin main
```

Inspect `status`, the staged diff, and `diff --check` before committing. If
`status` is empty, the project is already synchronized and no Overleaf commit
is required. Authentication uses the local Git credential store; never write
an Overleaf token into this repository.

### Recorded thesis-content synchronization

- Date: 2026-08-19
- Local source commit: `72e1264`
- Overleaf `main` commit: `200d70e`
