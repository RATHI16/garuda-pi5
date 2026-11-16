#!/bin/bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
mkdir -p "$root/.runtime/sim-truth"
c++ -std=c++17 -shared -fPIC "$root/bench/sim_truth/truth.cc" -o "$root/.runtime/sim-truth/libBenchTruth.so" $(pkg-config --cflags --libs gz-sim8 gz-plugin2 gz-transport13 gz-msgs10)
