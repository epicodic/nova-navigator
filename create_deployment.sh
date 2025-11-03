#!/bin/bash
DIST_DIR="dist"
mkdir -p $DIST_DIR
uvx --from shiv shiv . --site-packages ./.venv --compile-pyc  --compressed -o $DIST_DIR/nn -c nn
