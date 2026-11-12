#!/bin/bash
# Полное удаление ai-search: служба, команды /search, индекс. Папку с программой удалите сами.
set -uo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.local.ai-search"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Фоновая служба остановлена."
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
[ "$(readlink "$HOME/.local/bin/ai-search" 2>/dev/null)" = "$APP/ai-search" ] && rm -f "$HOME/.local/bin/ai-search"

for pair in "claude:$HOME/.claude/skills/search" "codex:$HOME/.codex/skills/search"; do
  name="${pair%%:*}"; d="${pair#*:}"
  if grep -q "ai-search" "$d/SKILL.md" 2>/dev/null; then
    rm -rf "$d"
    bak="$HOME/.ai-search/backup/$name-search"
    [ -e "$bak" ] && mv "$bak" "$d" && echo "Восстановлен прежний $d"
  fi
done
grep -q "ai-search" "$HOME/.gemini/commands/search.toml" 2>/dev/null && rm -f "$HOME/.gemini/commands/search.toml"
echo "Команды /search удалены."

read -r -p "Удалить индекс (~/.ai-search) и скачанную модель bge-m3 (~2.3 ГБ)? [y/N] " ans
if [[ "$ans" =~ ^[yYдД] ]]; then
  rm -rf "$HOME/.ai-search"
  rm -rf "$HOME/.cache/huggingface/hub/models--BAAI--bge-m3"
  echo "Индекс и модель удалены."
fi
echo "Готово. Папку $APP можно удалить вручную."
