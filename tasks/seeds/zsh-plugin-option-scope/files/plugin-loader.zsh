# Workspace prompt plugin loader. This file is sourced from .zshrc.
typeset -g WORKSPACE_PLUGIN_HOME=${${(%):-%N}:A:h}
fpath=("$WORKSPACE_PLUGIN_HOME/functions" $fpath)

autoload -Uz workspace_roots
autoload -Uz add-zsh-hook
source "$WORKSPACE_PLUGIN_HOME/plugin.zsh"
