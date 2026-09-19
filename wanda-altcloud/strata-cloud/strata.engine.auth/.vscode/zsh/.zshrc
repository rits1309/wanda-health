# VS Code workspace terminal startup for summit-backend-playground.
#
# The "zsh (.venv)" terminal profile points ZDOTDIR here, so zsh loads THIS
# file instead of ~/.zshrc. We deliberately:
#   1. source the user's real shell config first (pyenv, aliases, prompt, ...)
#   2. activate the project's .venv LAST, so its bin/ wins on PATH even if the
#      user's config (e.g. `pyenv init`) re-prepends the pyenv shims.
#
# This is what guarantees `python`/`pip` resolve to .venv in every new terminal.

# Restore ZDOTDIR so any nested/re-exec'd shell uses the user's normal config.
export ZDOTDIR="$HOME"

for _rc in "$HOME/.zshenv" "$HOME/.zprofile" "$HOME/.zshrc"; do
  [[ -r "$_rc" ]] && source "$_rc"
done
unset _rc

# Activate the project virtualenv if it exists.
# (Create it first with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
if [[ -n "$VENV_ACTIVATE" && -r "$VENV_ACTIVATE" ]]; then
  source "$VENV_ACTIVATE"
fi
