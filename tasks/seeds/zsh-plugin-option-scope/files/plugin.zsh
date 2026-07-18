# Hook wiring and prompt-facing summary. Directory selection belongs to the
# autoloaded helper so other plugins can reuse it without loading this hook.
workspace_status_precmd() {
  local base=${WORKSPACE_SCAN_ROOT:-$PWD}
  workspace_roots "$base"
  typeset -g WORKSPACE_PLUGIN_COUNT=${#reply}
  typeset -g WORKSPACE_PLUGIN_NAMES=${(j:,:)${reply:t}}
}

add-zsh-hook precmd workspace_status_precmd
