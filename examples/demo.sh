#!/usr/bin/env bash
# One-command demo: build a throwaway git repo for the shop service with a
# "PR" branch, record the PR's test, and print the path of the HTML viewer.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
wiz_root=$(dirname "$here")
repo="${1:-$wiz_root/.wiz/demo-shop}"
wiz="${WIZ:-$wiz_root/.venv/bin/wiz}"
git_c=(git -c user.name=demo -c user.email=demo@localhost -c commit.gpgsign=false)

rm -rf "$repo"
mkdir -p "$repo"
cp -r "$here/shop/." "$repo/"
cd "$repo"
find . -name __pycache__ -prune -exec rm -rf {} +
printf '__pycache__/\n.wiz/\n' > .gitignore
git init -q -b main
git apply -R "$here/shop-pr.patch"            # the code before the PR
"${git_c[@]}" add -A
"${git_c[@]}" commit -qm "Shop before the PR"
git checkout -qb free-shipping
git apply "$here/shop-pr.patch"               # the PR
"${git_c[@]}" commit -qam "Ship free over 50, add a heavy parcel surcharge and coupon minimums"

"$wiz" run tests/test_orders.py::test_order_over_50_ships_free_with_coupon --base main --out "$wiz_root/.wiz"
