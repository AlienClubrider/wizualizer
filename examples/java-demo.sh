#!/usr/bin/env bash
# Java demo (Maven): build a throwaway git repo for the Java shop with a "PR"
# branch, record the PR's test, and print the path of the HTML viewer.
# Needs a JDK (17+) and Maven; the first run downloads JUnit and the Maven plugins.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
wiz_root=$(dirname "$here")
repo="${1:-$wiz_root/.wiz/demo-java-shop}"
wiz="${WIZ:-$wiz_root/.venv/bin/wiz}"
git_c=(git -c user.name=demo -c user.email=demo@localhost -c commit.gpgsign=false)

rm -rf "$repo"
mkdir -p "$repo"
cp -r "$here/java-shop/." "$repo/"
cd "$repo"
rm -rf target
printf 'target/\n.wiz/\n' > .gitignore
git init -q -b main
git apply -R "$here/java-shop-pr.patch"       # the code before the PR
"${git_c[@]}" add -A
"${git_c[@]}" commit -qm "Shop before the PR"
git checkout -qb free-shipping
git apply "$here/java-shop-pr.patch"          # the PR
"${git_c[@]}" commit -qam "Ship free over 50, add a heavy parcel surcharge and coupon minimums"

"$wiz" run 'shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon' --base main --out "$wiz_root/.wiz"
