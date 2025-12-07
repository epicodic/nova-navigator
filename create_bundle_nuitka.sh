#!/bin/bash
uv run nuitka --standalone --onefile --follow-imports --include-data-files=src/nova_navigator/nn.tcss=nn.tcss src/nova_navigator/main.py
