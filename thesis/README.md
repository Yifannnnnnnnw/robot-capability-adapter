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
