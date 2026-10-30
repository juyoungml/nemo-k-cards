# Nemo K Cards revised presentation

`Nemo-K-Cards-Revised.pdf` is the current presentation: nine pages including the cover, with slides numbered 01–08. The original ten-page PDF remains at `../Nemo-K-Cards-Presentation.pdf`.

The revision broadens the examples to Korean travel and everyday information, uses actual user-provided Reddit and official-page screenshots, and combines research, English card creation and operator approval into slide 04. Slide 03 retains the original statistics with uniform chart styling. Slide 05 is now about the Safeguard (content blocking): two layers, a binding block nobody can override, fail-closed, re-checked per version, and explained findings, shown on a capture of the current Admin. Slide 01 lists the features added since (Guide cards, photo swap and slide rewrite, scheduled posts). The OpenShell policy/log evidence is retained. The architecture and feature descriptions reflect the reviewed implementation, without claiming deployment or live publishing verification.

## Files

- `render.m`: base-page revisions drawn over the original PDF.
- `merged-slide.m`: integrated research, illustrated Climate Card example and approval slide.
- `assemble-final.m`: final page selection, numbering and viewer-compatible PDF assembly.
- `assets/`: the five images required to rebuild, including the final English guide with its cash-backup note.
- `speaker-notes.txt`: suggested 6:35 talk, leaving 25 seconds within seven minutes; slide 05 (Safeguard) gets 90 seconds.

## Slide 05 assets

`assets/safeguard-blocked-admin.png` and `assets/safeguard-approve-locked.png` are captures of the current Admin (upstream `main`, 2026.10.30) on its demo data: the "bad" scenario's blocked Deck. The verdict text is the project's fixed test case (`backend/golden/recordings/safeguard-tank-day-sale.json`), not a block on a real post.
- `reddit-sources.json`, `climate-card-sources.json`: source metadata and the scope of the examples.

The English guide is an illustrated concept, not a result from a live agent run or a published Instagram post. Its airport-to-city scene is illustrative. The Safeguard captures on slide 05 show demo data, as noted above.

## Rebuild

Requires macOS, Xcode command-line tools (`clang` with AppKit, PDFKit and ImageIO), and Poppler (`pdftoppm`). From the repository root:

```sh
sh presentation/revised/rerender.sh
```

The build writes only `Nemo-K-Cards-Revised.pdf` in this directory. Intermediate PDFs, page images and compiled executables are created in a temporary directory and removed on exit. The final PDF uses rendered page images to preserve the checked appearance across viewers; its text is not selectable. The rendering source and original images remain available for edits.
