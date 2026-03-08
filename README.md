# 🎵 Discord Music Bot

Полнофункциональный музыкальный бот для Discord с поддержкой YouTube, SoundCloud, Spotify и множеством аудиоэффектов.

---

## 🚀 Быстрый старт

### 1. Клонируй / скачай файлы
```
bot.py, requirements.txt, nixpacks.toml, Procfile, .env.example
```

### 2. Настрой .env
```bash
cp .env.example .env
# Отредактируй .env и вставь свой DISCORD_TOKEN
```

### 3. Запусти локально
```bash
pip install -r requirements.txt
# Убедись что FFmpeg установлен: https://ffmpeg.org/download.html
python bot.py
```

---

## ☁️ Деплой на Railway

1. Создай новый проект на [railway.app](https://railway.app)
2. Подключи GitHub репозиторий (или загрузи файлы напрямую)
3. В разделе **Variables** добавь переменные из `.env.example`
4. Railway автоматически прочтёт `nixpacks.toml` и установит FFmpeg
5. Убедись что **Service Type** = `Worker` (не Web), потому что бот не слушает HTTP-порт

---

## 🎛️ Получение токенов

### Discord Token
1. Зайди на [discord.com/developers/applications](https://discord.com/developers/applications)
2. New Application → Bot → Reset Token
3. Включи **Privileged Gateway Intents**: Message Content Intent, Server Members Intent, Presence Intent
4. Для инвайта: OAuth2 → URL Generator → `bot` + `applications.commands` → Permissions: Connect, Speak, Use Voice Activity, Send Messages, Embed Links, Read Message History

### Spotify (опционально)
1. Зайди на [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create app → скопируй Client ID и Client Secret

---

## 📋 Все команды

| Команда | Описание |
|---------|----------|
| `!play <запрос/ссылка>` | Играть трек или плейлист |
| `!playtop <запрос>` | Добавить в начало очереди |
| `!search <запрос>` | Поиск с выбором из 5 результатов |
| `!skip [N]` | Пропустить N треков |
| `!skipto <N>` | Перейти к треку #N |
| `!pause` / `!resume` | Пауза / Продолжить |
| `!stop` | Остановить и отключиться |
| `!queue [стр.]` | Показать очередь |
| `!nowplaying` | Текущий трек + прогресс-бар |
| `!shuffle` | Перемешать очередь |
| `!clear` | Очистить очередь |
| `!remove <N>` | Удалить трек по номеру |
| `!move <откуда> <куда>` | Переместить трек в очереди |
| `!loop [off/one/queue]` | Режим повтора |
| `!bassboost` | 🎸 Bass Boost |
| `!nightcore` | 🌙 Nightcore |
| `!vaporwave` | 🌊 Vaporwave |
| `!treble` | 🎶 Treble Boost |
| `!echo` | 🌀 Эхо |
| `!speed [0.5-2.0]` | ⚡ Скорость воспроизведения |
| `!volume [0-200]` | 🔊 Громкость |
| `!effects` | Показать все эффекты |
| `!resetfx` | Сбросить все эффекты |
| `!ping` | Задержка бота |

---

## 🎵 Поддерживаемые сервисы
- **YouTube** (треки, плейлисты)
- **SoundCloud** (треки, плейлисты)
- **Spotify** (треки, плейлисты, альбомы) — через Spotify API + поиск на YT
- **Bandcamp**
- **Twitch**
- **Vimeo**
- И всё, что поддерживает [yt-dlp](https://github.com/yt-dlp/yt-dlp)
