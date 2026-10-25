#!/usr/bin/env bash
# reco-act 폐쇄망 설치. 번들 디렉터리 안에서 실행한다(INSTALL.md, 01-plan 4.4·6장).
#   ./install.sh --sums-sha256 <값> [--home /opt/reco-act] [--verify-only] [--no-load]
# --sums-sha256: 번들을 만든 사람이 번들과 따로 알려 준 SHA256SUMS의 sha256. 주지 않으면 SHA256SUMS 자체는 확인하지 못한다.
# 하는 일: 1) SHA256SUMS로 모든 파일 확인 2) docker load 3) 소스를 <home>/app/reco-act-<버전>에 푼다
#          4) models/를 <home>/models로 복사하고 대조한다(같은 파일은 건너뜀) 5) 데이터 디렉터리와 설정 파일 견본(없을 때만)
# 기존 설정 파일과 데이터(records, pgdata, redis)는 건드리지 않는다. 필요한 것: bash, coreutils, tar, docker.
set -euo pipefail

home="${RECO_ACT_HOME:-/opt/reco-act}"
sums_sha256=""
verify_only=0
load=1
while [ $# -gt 0 ]; do
  case "$1" in
    --home) home="$2"; shift 2 ;;
    --sums-sha256) sums_sha256="$2"; shift 2 ;;
    --verify-only) verify_only=1; shift ;;
    --no-load) load=0; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$home" in /*) ;; *) home="$PWD/$home" ;; esac  # 아래에서 번들 디렉터리로 옮겨 가므로
sums_sha256="$(printf '%s' "$sums_sha256" | tr 'A-F' 'a-f')"
cd "$(dirname "$0")"
say() { echo "[install] $*"; }
fail() { echo "[install] $*" >&2; exit 1; }
# SHA256SUMS에서 경로가 <접두어>로 시작하는 줄("sha256  경로")
listed() { grep -E "^[0-9a-f]{64}  $1" SHA256SUMS || true; }

[ -f SHA256SUMS ] && [ -f manifest.json ] || fail "run this inside the bundle directory (SHA256SUMS, manifest.json)"
version="$(sed -n 's/^  "version": "\(.*\)",$/\1/p' manifest.json)"
[ -n "$version" ] || fail "manifest.json has no version"
say "reco-act airgap bundle $version"

# ---------- 1) 확인 ----------
if [ -n "$sums_sha256" ]; then
  actual="$(sha256sum SHA256SUMS | cut -d' ' -f1)"
  [ "$actual" = "$sums_sha256" ] || fail "SHA256SUMS sha256 is $actual, expected $sums_sha256. Do not install this bundle"
else
  echo "[install] warning: --sums-sha256 not given, SHA256SUMS itself is not checked" >&2
fi
if grep -q '"skipped": \[$' manifest.json; then
  echo "[install] warning: incomplete bundle (rehearsal); see \"skipped\" in manifest.json" >&2
fi
say "checking every file against SHA256SUMS (reads the whole bundle)"
sha256sum --quiet --strict -c SHA256SUMS || fail "bundle check failed. Do not install this bundle"
extra="$(comm -23 <(find . -type f ! -path ./SHA256SUMS | sed 's|^\./||' | LC_ALL=C sort) \
                  <(sed 's/^[0-9a-f]\{64\}  //' SHA256SUMS | LC_ALL=C sort))"
if [ -n "$extra" ]; then
  echo "[install] warning: files not in SHA256SUMS are ignored:" >&2
  echo "$extra" | sed 's/^/  /' >&2
fi
say "bundle OK"
[ "$verify_only" = 0 ] || exit 0

mkdir -p "$home"/{app,records,models,pgdata,redis,tls}

# ---------- 2) 이미지 ----------
if [ "$load" = 1 ]; then
  command -v docker >/dev/null || fail "docker not found"
  while read -r _ path; do
    say "docker load $path"
    docker load -i "$path"
  done < <(listed images/)
else
  say "skipping docker load (--no-load)"
fi

# ---------- 3) 소스(compose 파일, 설정 견본) ----------
source_tar="$(listed source/ | sed 's/^[0-9a-f]\{64\}  //')"
[ -n "$source_tar" ] || fail "no source archive in SHA256SUMS"
tar -xzf "$source_tar" -C "$home/app"
app="$home/app/reco-act-$version"
[ -f "$app/docker-compose.airgap.yaml" ] || fail "$app/docker-compose.airgap.yaml not found after extracting $source_tar"
say "source -> $app"

# ---------- 4) 모델 ----------
copied=0
kept=0
while read -r sum path; do
  target="$home/$path"
  if [ -f "$target" ] && [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$sum" ]; then
    kept=$((kept + 1))
    continue
  fi
  mkdir -p "$(dirname "$target")"
  cp "$path" "$target.part"
  mv "$target.part" "$target"
  [ "$(sha256sum "$target" | cut -d' ' -f1)" = "$sum" ] || fail "copied model file does not match: $target"
  copied=$((copied + 1))
done < <(listed models/)
say "models -> $home/models ($copied copied, $kept already there)"

# ---------- 5) 설정 파일 견본(없을 때만) ----------
created=()
for name in compose airgap db; do
  target="$home/$name.env"
  [ -e "$target" ] && continue
  cp "$app/deploy/airgap/$name.env.example" "$target"
  chmod 600 "$target"
  created+=("$target")
  # 새로 만든 compose.env에는 이 번들의 버전과 설치 위치를 넣는다
  if [ "$name" = compose ]; then
    sed -i "s|^RECO_ACT_VERSION=.*|RECO_ACT_VERSION=$version|; s|^RECO_ACT_HOME=.*|RECO_ACT_HOME=$home|" "$target"
  fi
done
current="$(sed -n 's/^RECO_ACT_VERSION=//p' "$home/compose.env")"

compose="docker compose -f docker-compose.airgap.yaml --env-file $home/compose.env"
say "done"
if [ ${#created[@]} -gt 0 ]; then
  say "fill in the new settings (CHANGE_ME, mail server): ${created[*]}"
fi
if [ "$current" != "$version" ]; then
  say "set RECO_ACT_VERSION=$version in $home/compose.env (now $current)"
fi
[ -f "$home/tls/server.crt" ] && [ -f "$home/tls/server.key" ] || say "put the internal CA certificate in $home/tls/server.crt and server.key"
say "start: cd $app && $compose up -d --no-build"
