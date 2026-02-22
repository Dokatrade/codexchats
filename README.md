# Codex Chats Local Archive (MVP)

Локальное приложение для индексации и просмотра `codex` чатов из нескольких WSL источников (`\\wsl.localhost\...`).

## Быстрый старт

1. Создай venv и установи зависимости:
   ```bash
   python -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
2. Скопируй `config.example.json` в `config.json` и проверь пути.
3. Запусти:
   ```bash
   .venv/bin/python -m app.main
   ```
4. Открой `http://127.0.0.1:8000`.

## Что умеет MVP

- Full scan на первом запуске
- Quick scan на последующих запусках
- Инкрементальное обновление только измененных файлов
- Поиск по сообщениям (SQLite FTS5)
- Просмотр сессий и сообщений
- Ручной `Quick Rescan` / `Full Rescan`

## Ограничения MVP

- Парсер формата `.codex/sessions` сделан с эвристиками (generic/fallback).
- Для нестандартных файлов часть событий может не попасть в `messages`.
- Polling по умолчанию выключен.

