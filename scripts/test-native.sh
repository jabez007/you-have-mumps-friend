#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Every run gets its own database. Never point these tests at application data.
set -euo pipefail
: "${ydb_dist:?Set ydb_dist to an installed YottaDB distribution}"
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d /tmp/yhmf-native.XXXXXXXX)
export ydb_gbldir="$test_dir/test.gld"
export ydb_routines="$ydb_dist"
if [[ -f "$ydb_dist/libyottadbutil.so" ]]; then
    export ydb_routines="$ydb_dist/libyottadbutil.so $ydb_dist"
fi
export LD_LIBRARY_PATH="$ydb_dist${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export YHMF_TEST_NATIVE=1

cleanup() {
    "$ydb_dist/mupip" rundown -region '*' >/dev/null 2>&1 || true
    rm -rf -- "$test_dir"
}
trap cleanup EXIT

"$ydb_dist/yottadb" -run GDE <<EOF
change -segment DEFAULT -file=$test_dir/test.dat
change -region DEFAULT -key_size=1019 -record_size=32767
exit
EOF
"$ydb_dist/mupip" create
if [[ "${YHMF_JOURNALING:-0}" == "1" ]]; then
    "$ydb_dist/mupip" set -journal="enable,on,before,file=$test_dir/test.mjl" -region '*'
    test -f "$test_dir/test.mjl"
fi
"$project_dir/.venv/bin/python" -c 'import yottadb; print(yottadb.release())'
# A fresh disposable database prevents newer checkpoints contaminating an old client.
if [[ -n "${YHMF_CONSUMER_MODE:-}" ]]; then
    artifact_dir=$(cd -- "$YHMF_ARTIFACT_DIR" && pwd)
    "$project_dir/.venv/bin/python" "$project_dir/scripts/qualify_consumer.py" "$artifact_dir"/*.whl --mode "$YHMF_CONSUMER_MODE" --native
    exit
fi

"$project_dir/.venv/bin/pytest" "$project_dir/tests" "$@"

if [[ -n "${YHMF_ARTIFACT_DIR:-}" ]]; then
    artifact_dir=$(cd -- "$YHMF_ARTIFACT_DIR" && pwd)
    (cd "$artifact_dir" && sha256sum -c SHA256SUMS)
    "$project_dir/.venv/bin/python" "$project_dir/scripts/qualify_install.py" "$artifact_dir"/*.whl --native
    "$project_dir/.venv/bin/python" "$project_dir/scripts/qualify_install.py" "$artifact_dir"/*.tar.gz --native
fi

if [[ "${YHMF_BENCHMARK:-0}" == "1" ]]; then
    "$project_dir/.venv/bin/python" "$project_dir/scripts/benchmark-mutations.py"
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m examples.inventory --native)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m scripts.benchmark_inventory)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m scripts.benchmark_rollups)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m scripts.benchmark_maintenance)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m scripts.benchmark_m_writers)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m examples.inventory.bins --native)
    (cd "$project_dir" && "$project_dir/.venv/bin/python" -m scripts.benchmark_bin_boundary)
fi

if [[ "${YHMF_CONSUMER:-0}" == "1" ]]; then
    for mode in fresh upgrade; do
        YHMF_CONSUMER_MODE="$mode" bash "$project_dir/scripts/test-native.sh"
    done
fi
