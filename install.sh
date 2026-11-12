#!/bin/bash
# Установка ai-search: от А до Я. Повторный запуск безопасен (обновляет установку).
set -euo pipefail
APP="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.local.ai-search"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DATA="$HOME/.ai-search"
step() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }

[ "$(uname)" = "Darwin" ] || { echo "Нужен macOS."; exit 1; }
mkdir -p "$DATA/logs"

step "1/7  Менеджер Python-окружений uv"
if ! command -v uv >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then brew install uv
  else curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; fi
fi
uv --version

step "2/7  Python 3.12 и библиотеки (torch, sentence-transformers, sqlite-vec, watchdog…)"
# (re)create the environment if it's missing or was copied from another Mac and is broken
"$APP/.venv/bin/python" -c "pass" 2>/dev/null || uv venv -q --clear --python 3.12 "$APP/.venv"
uv pip install -q --python "$APP/.venv/bin/python" -r "$APP/requirements.txt"
chmod +x "$APP/ai-search" "$APP/uninstall.sh"
# Команда ai-search доступна из любой папки
mkdir -p "$HOME/.local/bin"
ln -sf "$APP/ai-search" "$HOME/.local/bin/ai-search"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Добавьте ~/.local/bin в PATH, чтобы писать просто ai-search:"
     echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
esac
echo "Готово."

step "3/7  Модель эмбеддингов bge-m3 (~2.3 ГБ, скачивается один раз)"
"$APP/ai-search" download-model

step "4/7  Остановка старой фоновой службы (если была)"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "Остановлена." || echo "Не было."

step "5/7  Индексация переписок с ИИ-агентами (обычно несколько минут)"
"$APP/ai-search" index --only chats

step "6/7  Фоновая служба (автозапуск при входе в систему)"
mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__APP__|$APP|g" -e "s|__DATA__|$DATA|g" -e "s|__LABEL__|$LABEL|g" "$APP/templates/launchd.plist" > "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Запущена: $LABEL"
echo "Файлы проектов она проиндексирует сама в фоне, начиная с самых свежих."
echo "Прогресс: \"$APP/ai-search\" status"

step "7/7  Команда /search для ИИ-агентов"
render() { # $1 = how the agent passes the question
  sed -e "s|__APP__|$APP|g" -e "s|__QUESTION__|$1|g" "$APP/templates/instructions.md"; }
# A pre-existing, unrelated /search skill is moved to ~/.ai-search/backup (outside the skills folder,
# so the agent doesn't load two "search" skills); uninstall.sh puts it back.
backup() {
  local dst="$DATA/backup/$2"
  if [ -e "$1" ] && ! grep -q "ai-search\" search" "$1/SKILL.md" 2>/dev/null && [ ! -e "$dst" ]; then
    mkdir -p "$DATA/backup"; mv "$1" "$dst"; echo "Прежний $1 сохранён в $dst"
  else rm -rf "$1"; fi
}

if [ -d "$HOME/.claude" ]; then
  backup "$HOME/.claude/skills/search" claude-search; mkdir -p "$HOME/.claude/skills/search"
  { printf -- "---\nname: search\ndescription: Answer the user's question after searching their past AI-agent conversations and project files with the local ai-search index, then Spotlight. Use only when the user types /search <question>.\nargument-hint: <вопрос>\n---\n\n"
    render '$ARGUMENTS'; } > "$HOME/.claude/skills/search/SKILL.md"
  echo "Claude Code:  /search <вопрос>"
fi
if [ -d "$HOME/.codex" ]; then
  backup "$HOME/.codex/skills/search" codex-search; mkdir -p "$HOME/.codex/skills/search"
  { printf -- "---\nname: search\ndescription: Search the user's past AI-agent conversations and project files with the local ai-search index before answering. Use when the user invokes \$search or asks to look through past chats or their computer.\n---\n\n"
    render "the user's message that invoked this skill"; } > "$HOME/.codex/skills/search/SKILL.md"
  echo "Codex:        \$search <вопрос>"
fi
if [ -d "$HOME/.gemini" ]; then
  mkdir -p "$HOME/.gemini/commands"
  { printf 'description = "Поиск по прошлым перепискам с ИИ-агентами и файлам проектов (ai-search)"\nprompt = """\n'
    render '{{args}}'; printf '"""\n'; } > "$HOME/.gemini/commands/search.toml"
  echo "Gemini CLI:   /search <вопрос>"
fi
echo "Любой другой агент: попросите его запустить  \"$APP/ai-search\" search \"<вопрос>\""

sleep 3
printf "\n\033[1m✅ Установка завершена.\033[0m\n\n"
"$APP/ai-search" status || true
cat <<EOF

Проверка:   "$APP/ai-search" search "что я делал на прошлой неделе"
Удаление:   "$APP/uninstall.sh"

Если macOS спросит «python хочет получить доступ к папке Рабочий стол», нажмите «Разрешить»:
без этого фоновая служба не увидит новые файлы на Desktop.
EOF
