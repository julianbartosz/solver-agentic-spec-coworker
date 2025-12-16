#!/usr/bin/env bash
set -euo pipefail

# Create a per-run branch in a realism repo and then run the Integration Co-Worker CLI
# against that repo so repeated manual testing is safe and diffs are isolated.
#
# Usage:
#   scripts/run_realism_repo_branch.sh \
#     --repo-root /path/to/realism-repo \
#     --provider my_provider \
#     --spec-ref /path/to/spec.json \
#     --task "Do the thing" \
#     [--extra "--strict-codegen"]

repo_root=""
provider=""
spec_ref=""
task=""
extra_args=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      repo_root="$2"; shift 2 ;;
    --provider)
      provider="$2"; shift 2 ;;
    --spec-ref)
      spec_ref="$2"; shift 2 ;;
    --task)
      task="$2"; shift 2 ;;
    --extra)
      extra_args="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,120p' "$0" | sed -n '1,40p'
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$repo_root" || -z "$provider" || -z "$spec_ref" || -z "$task" ]]; then
  echo "Missing required args." >&2
  echo "Required: --repo-root --provider --spec-ref --task" >&2
  exit 2
fi

if [[ ! -d "$repo_root" ]]; then
  echo "Repo root not found: $repo_root" >&2
  exit 2
fi

if [[ ! -f "$spec_ref" ]]; then
  echo "Spec ref file not found: $spec_ref" >&2
  exit 2
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ts="$(date +%Y%m%d-%H%M%S)"
branch="coworker/${provider}/${ts}"
log_dir="${root_dir}/logs/realism"
mkdir -p "$log_dir"
log_file="${log_dir}/${provider}-${ts}.log"

echo "== Realism repo branch run =="
echo "repo_root: $repo_root"
echo "provider : $provider"
echo "spec_ref : $spec_ref"
echo "task     : $task"
echo "branch   : $branch"
echo "log_file : $log_file"
echo

echo "-- Creating branch in realism repo --"
pushd "$repo_root" >/dev/null

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Not a git repo: $repo_root" >&2
  popd >/dev/null
  exit 2
fi

# Ensure we have a baseline branch we can always branch from.
if ! git show-ref --verify --quiet refs/heads/baseline; then
  git branch baseline
fi

# Always branch from baseline to avoid accumulating changes.
git checkout baseline >/dev/null 2>&1 || git checkout -b baseline
git checkout -b "$branch"

popd >/dev/null

echo "-- Running coworker CLI (output -> log file) --"

start_epoch="$(date +%s)"

# Prefer project venv if present.
python_bin="${root_dir}/.venv311/bin/python"
if [[ ! -x "$python_bin" ]]; then
  python_bin="python"
fi

set +e
"$python_bin" -m integration_coworker.cli run \
  --spec-ref "$spec_ref" \
  --task "$task" \
  --provider "$provider" \
  --repo-root "$repo_root" \
  $extra_args \
  2>&1 | tee "$log_file"
exit_code=${PIPESTATUS[0]}
set -e

end_epoch="$(date +%s)"
runtime_s=$((end_epoch - start_epoch))

echo
echo "== Summary =="
echo "exit_code: ${exit_code}"
echo "runtime_s: ${runtime_s}"
echo "branch   : ${branch}"
echo "log_file : ${log_file}"

if [[ $exit_code -eq 0 ]]; then
  echo
  echo "Next: review changes"
  echo "  cd \"$repo_root\""
  echo "  git status"
  echo "  git diff baseline...HEAD"
else
  echo "Run failed (non-zero exit). See log: $log_file" >&2
fi

exit "$exit_code"
