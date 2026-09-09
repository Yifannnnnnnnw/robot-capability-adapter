#!/usr/bin/env bash
# Full 4-pass build (pdflatex -> bibtex -> pdflatex x2) + sanity checks.
# A single pdflatex pass leaves all \ref/\cite as "??" -- always use this script.
set -e
cd "$(dirname "$0")"

rm -f main.aux main.bbl main.blg main.out
pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
bibtex main >/dev/null
pdflatex -interaction=nonstopmode main.tex >/dev/null
pdflatex -interaction=nonstopmode main.tex >/dev/null

# Sanity: no unresolved refs/citations; main content (§1-§7, through Summary)
# must fit in 6 pages. Limitations/References are off-budget. Sufficient test:
# the last numbered main heading "Summary" AND the "Limitations" heading both
# appear on page 6 -- so everything before Limitations (= all main content)
# fits within 6 pages. (Strip the review line-number ruler before matching.)
TEXT=$(pdftotext main.pdf - 2>/dev/null)
QQ=$(echo "$TEXT" | grep -o '??' | wc -l | tr -d ' ')
CQ=$(echo "$TEXT" | grep -o '(?)' | wc -l | tr -d ' ')
PAGES=$(pdfinfo main.pdf | awk '/^Pages/{print $2}')
P6=$(pdftotext -f 6 -l 6 main.pdf - 2>/dev/null | sed -E 's/^[0-9]+ *//')
SUM6=$(echo "$P6" | grep -c '^Summary' || true)
LIM6=$(echo "$P6" | grep -c '^Limitations' || true)
# Main content (through §7 Summary) must fit in 6 pages. Summary must be on
# page 6, and the first real line of page 7 must be the Limitations heading
# (so no main-body text spilled past p6). Limitations may sit on p6 or p7.
P7FIRST=$(pdftotext -f 7 -l 7 main.pdf - 2>/dev/null | sed -E 's/^[0-9]+ *//' | grep -vE '^[[:space:]]*$' | head -1)
MAINFIT=$([ "$SUM6" -ge 1 ] && { [ "$LIM6" -ge 1 ] || [ "$P7FIRST" = "Limitations" ]; } && echo 1 || echo 0)

echo "pages=$PAGES  unresolved_refs(??)=$QQ  unresolved_cites((?))=$CQ  main_fits_6pp=$MAINFIT (Summary_p6=$SUM6 Limitations_p6=$LIM6 p7first=$P7FIRST)"
[ "$QQ" = 0 ] || { echo "FAIL: unresolved cross-references"; exit 1; }
[ "$CQ" = 0 ] || { echo "FAIL: unresolved citations"; exit 1; }
[ "$MAINFIT" = 1 ] || { echo "FAIL: main text spills past page 6 (page-7 starts with: $P7FIRST)"; exit 1; }
echo "OK: main.pdf is good"
