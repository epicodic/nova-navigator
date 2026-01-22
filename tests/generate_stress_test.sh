#!/bin/bash

FILE_COUNT=10000

MANY_FILES_DIR="stress_test/directory_with_${FILE_COUNT}_files"
# generate files
mkdir -p "$MANY_FILES_DIR"
for i in $(seq 1 $FILE_COUNT); do
    echo "This is file number $i" > "$MANY_FILES_DIR/file_$i.txt"
done

