
###############################################################################
# Preliminary checks and setup

# Ensure script was sourced and not executed
if [[ "${BASH_SOURCE[0]}" == "${0}" ]] || [[ "${ZSH_EVAL_CONTEXT:-}" == 'toplevel' ]]; then
    echo -e "\033[1;31mError: This script must be sourced, not executed. Use 'source activate.sh' or '. activate.sh'\033[0m"
    exit 1
fi

# Determine WORKSPACE_ROOT
if [ "$GITHUB_ACTIONS" = "true" ]; then
    # Setup Workspace folder
    WORKSPACE_ROOT="$GITHUB_WORKSPACE"
elif [ -n "${BASH_SOURCE[0]}" ]; then
    # BASH
    WORKSPACE_ROOT="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
else
    # ZSH or posix compatible shells
    WORKSPACE_ROOT="$(dirname "$(realpath "$0")")"
fi

###############################################################################
# Main script logic
main() {


    ###############
    # Setup

    # Python environment variables

    # avoid __pycache__ directories being created in source directories
    export PYTHONPYCACHEPREFIX="$WORKSPACE_ROOT/.cache/pycache"

    # avoid adding the project root to sys.path, which mimics the behavior of
    # Bazel and prevents accidental imports from source directories
    export PYTHONSAFEPATH="1"
   
    # activate Python virtual environment
    . "$WORKSPACE_ROOT/.venv/bin/activate"



}

# putting the script content into a function to avoid polluting the global namespace
main
