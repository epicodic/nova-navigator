#!/bin/bash
DIST_DIR="dist"

BUNDLE_VENV_DIR="$DIST_DIR/venv"

mkdir -p $DIST_DIR

rm -rf $BUNDLE_VENV_DIR
uv venv $BUNDLE_VENV_DIR
source $BUNDLE_VENV_DIR/bin/activate
uv sync --active --no-dev
uvx --from shiv shiv . --site-packages $BUNDLE_VENV_DIR --compile-pyc  --compressed -o $DIST_DIR/nn -c nn

#uvx --from shiv shiv . --site-packages .venv --compile-pyc  --compressed -o $DIST_DIR/nn -c nn
#uvx --from shiv shiv . --site-packages foo --compile-pyc  --compressed -o $DIST_DIR/nn -c nn

#uv export --no-dev --no-hashes --format=requirements-txt  > $DIST_DIR/shiv_requirements.txt  
#uvx --from shiv shiv -r $DIST_DIR/shiv_requirements.txt --compile-pyc  --compressed -o $DIST_DIR/nn -c nn
#uvx --from shiv shiv -r requirements.txt --compile-pyc  --compressed -o $DIST_DIR/nn -c nn
