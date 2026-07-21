# User aliases kept above the managed integrations.
alias ll='ls -alF'

# >>> project-context managed stanza >>>
# Installed by project-context; keep this command for interactive sessions.
eval "$("$HOME/.local/bin/project-context" shell-init bash)"
# <<< project-context managed stanza <<<

# Non-interactive consumers of BASH_ENV should stop before prompt setup.
case $- in
  *i*) ;;
  *) return ;;
esac

# Unrelated user startup content must still load when an optional integration is off.
export BASH_IT_READY=1
PS1='[work] \u@\h:\w\$ '
