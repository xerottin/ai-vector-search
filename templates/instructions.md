The user's question: __QUESTION__

If the question is empty, ask what to search for and stop.

## 1. Past conversations and project files (local semantic index)
Run the local hybrid search. It matches by meaning (Russian and English together) and by exact words, and checks for fresh changes before it searches:

```
"__APP__/ai-search" search "<the question, rephrased as a short search query>"
```

Useful options: `--kind chats` (only past conversations with AI agents), `--kind files` (only project files), `-n 5` (fewer results, fewer tokens). Run a second, differently worded search only if the first one misses. Each result shows where it came from: the session file and its title, or the file path and its lines. When a hit looks important, read more around it: open the file at those lines, or `grep -n` the session file. Do not read huge files whole.

## 2. The rest of the computer (Spotlight)
Only if the question is about files, documents or projects and step 1 did not cover it:
`mdfind -onlyin ~ "<word>" | grep -v -e /Library/ -e node_modules | head -30`

Skip secrets (.env, keys, tokens) and never print their contents.

## 3. Answer
Answer in the user's language. Start with a short "**Что нашёл:**" block naming the relevant sessions (title, date) and files (paths), then give the answer itself, built on what you found. If nothing relevant was found, say so in one line and answer from general knowledge.

If the command fails because ai-search is not installed or not running, tell the user to run `"__APP__/install.sh"`, or `"__APP__/ai-search" start`.
