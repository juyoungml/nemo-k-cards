#!/bin/sh
set -eu
repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
render_tmp=$(mktemp -d /tmp/nemo-rerender.XXXXXX)
trap 'rm -rf "$render_tmp"' EXIT

clang -fno-modules -framework AppKit -framework PDFKit "$repo_root/presentation/revised/render.m" -o "$render_tmp/render"
"$render_tmp/render" "$repo_root" "$render_tmp"
pdftoppm -r 108 -png "$render_tmp/base.pdf" "$render_tmp/slide"

clang -fno-modules -framework AppKit -framework PDFKit "$repo_root/presentation/revised/merged-slide.m" -o "$render_tmp/merged"
"$render_tmp/merged" "$repo_root" "$render_tmp"
clang -fno-modules -framework AppKit -framework ImageIO "$repo_root/presentation/revised/assemble-final.m" -o "$render_tmp/assemble"
"$render_tmp/assemble" "$repo_root" "$render_tmp"
