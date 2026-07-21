# Login shells share the user's interactive Bash configuration.
if [[ -r "$HOME/.bashrc" ]]; then
  source "$HOME/.bashrc"
fi

export BASH_PROFILE_LOADED=1
