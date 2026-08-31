#!/usr/bin/env bash
set -euo pipefail

pyrefly_script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
pyrefly_app_root=$(cd "$pyrefly_script_dir/.." && pwd)
pyrefly_bench_root=$(cd "$pyrefly_app_root/../.." && pwd)
pyrefly_bench_executable="$pyrefly_bench_root/env/bin/pyrefly"
pyrefly_bench_python="$pyrefly_bench_root/env/bin/python"

cd "$pyrefly_app_root"

if [[ -x "$pyrefly_bench_executable" && -x "$pyrefly_bench_python" ]]; then
	exec "$pyrefly_bench_executable" check \
		--python-interpreter-path "$pyrefly_bench_python" \
		"$@"
fi

pyrefly_executable=$(command -v pyrefly || true)
pyrefly_python=$(command -v python3 || true)

if [[ -z "$pyrefly_executable" ]]; then
	printf 'Pyrefly is not installed in the Bench environment or on PATH.\n' >&2
	exit 1
fi

if [[ -z "$pyrefly_python" ]]; then
	printf 'Python 3 is not available on PATH for Pyrefly environment discovery.\n' >&2
	exit 1
fi

exec "$pyrefly_executable" check \
	--python-interpreter-path "$pyrefly_python" \
	"$@"
