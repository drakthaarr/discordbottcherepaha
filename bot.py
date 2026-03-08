"""
╔══════════════════════════════════════════════════════════╗
║           🎵 DISCORD MUSIC BOT — Railway Edition         ║
║  Supports: YouTube, SoundCloud, Spotify, and more        ║
║  Features: Queue, Loop, Bass Boost, Nightcore, Speed...  ║
╚══════════════════════════════════════════════════════════╝
"""

import discord
from discord.ext import commands, tasks
import asyncio
import yt_dlp
import os
import random
import re
import time
import math
from collections import deque
from dotenv import load_dotenv

# ── Optional Spotify support ──────────────────────────────
try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIPY_AVAILABLE = True
except ImportError:
    SPOTIPY_AVAILABLE = False

load_dotenv()

# ══════════════════════════════════════════════════════════
#  CONFIG — читается из .env
# ══════════════════════════════════════════════════════════
DISCORD_TOKEN       = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID   = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
PREFIX              = os.getenv("PREFIX", "!")
AUTO_LEAVE_DELAY    = int(os.getenv("AUTO_LEAVE_DELAY", "120"))   # секунды

if not DISCORD_TOKEN:
    raise RuntimeError("❌ DISCORD_TOKEN не найден в .env!")

# ── Spotify client ────────────────────────────────────────
sp = None
if SPOTIPY_AVAILABLE and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        ))
        print("✅ Spotify подключён")
    except Exception as e:
        print(f"⚠️  Spotify не подключён: {e}")

# ══════════════════════════════════════════════════════════
#  YT-DLP / FFMPEG НАСТРОЙКИ
# ══════════════════════════════════════════════════════════
YDL_BASE_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "source_address": "0.0.0.0",
    "default_search": "auto",
    "extract_flat": False,
}

FFMPEG_RECONNECT = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


def build_ffmpeg_opts(bass: bool = False, nightcore: bool = False,
                      speed: float = 1.0, volume: int = 100,
                      treble: bool = False, vaporwave: bool = False,
                      echo: bool = False) -> dict:
    """Строит опции FFmpeg под текущие эффекты."""
    filters = []

    if vaporwave:
        filters.append("asetrate=44100*0.8,aresample=44100,atempo=0.9")
    elif nightcore:
        filters.append("asetrate=44100*1.25,aresample=44100")
    elif speed != 1.0:
        # atempo допускает только 0.5..2.0 за один проход
        s = speed
        while s > 2.0:
            filters.append("atempo=2.0")
            s /= 2.0
        while s < 0.5:
            filters.append("atempo=0.5")
            s *= 2.0
        filters.append(f"atempo={round(s, 3)}")

    if bass:
        filters.append("bass=g=20,dynaudnorm=f=200")
    if treble:
        filters.append("treble=g=10")
    if echo:
        filters.append("aecho=0.8:0.9:1000:0.3")

    filters.append(f"volume={volume / 100:.2f}")

    af_str = ",".join(filters) if filters else None
    options = "-vn" + (f' -af "{af_str}"' if af_str else "")

    return {
        "before_options": FFMPEG_RECONNECT,
        "options": options,
    }


# ══════════════════════════════════════════════════════════
#  СОСТОЯНИЕ СЕРВЕРА
# ══════════════════════════════════════════════════════════
class GuildState:
    def __init__(self):
        self.queue:       deque  = deque()
        self.current:     dict   = None
        self.voice:       discord.VoiceClient = None
        self.text_ch:     discord.TextChannel  = None

        # Эффекты
        self.loop_mode:   str   = "off"   # off | one | queue
        self.bass:        bool  = False
        self.nightcore:   bool  = False
        self.treble:      bool  = False
        self.echo:        bool  = False
        self.vaporwave:   bool  = False
        self.speed:       float = 1.0
        self.volume:      int   = 100

        # Служебное
        self.playing:     bool  = False
        self.start_time:  float = None
        self.paused_at:   float = None
        self.total_paused:float = 0.0
        self.alone_since: float = None   # для авто-выхода


_states: dict[int, GuildState] = {}


def state(guild_id: int) -> GuildState:
    if guild_id not in _states:
        _states[guild_id] = GuildState()
    return _states[guild_id]


# ══════════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════
def fmt_dur(secs) -> str:
    if not secs:
        return "?:??"
    secs = int(secs)
    h, m = divmod(secs, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: float, total: float, width: int = 18) -> str:
    if not total:
        return "▬" * width
    pct = min(elapsed / total, 1.0)
    filled = int(pct * width)
    return "━" * filled + "🔘" + "─" * (width - filled)


def effect_badge(st: GuildState) -> str:
    parts = []
    if st.bass:       parts.append("🎸Bass")
    if st.nightcore:  parts.append("🌙NC")
    if st.treble:     parts.append("🎶Treble")
    if st.echo:       parts.append("🌀Echo")
    if st.vaporwave:  parts.append("🌊Vapor")
    if st.speed != 1.0: parts.append(f"⚡{st.speed}x")
    return " • ".join(parts) if parts else "—"


def loop_badge(mode: str) -> str:
    return {"off": "➡️ Off", "one": "🔂 One", "queue": "🔁 Queue"}.get(mode, "—")


async def fetch_info(query: str, playlist: bool = False) -> dict | None:
    opts = {**YDL_BASE_OPTS, "noplaylist": not playlist}
    if not query.startswith("http"):
        query = f"ytsearch:{query}"
    loop = asyncio.get_event_loop()
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await loop.run_in_executor(
                None, lambda: ydl.extract_info(query, download=False)
            )
        return info
    except Exception as e:
        print(f"[yt-dlp] {e}")
        return None


async def fetch_spotify(url: str) -> list[str]:
    """Возвращает список строк '<artist> <title>' для поиска на YT."""
    if not sp:
        return []
    tracks = []
    try:
        if "track" in url:
            m = re.search(r"track/([A-Za-z0-9]+)", url)
            if m:
                t = sp.track(m.group(1))
                tracks.append(f"{t['artists'][0]['name']} {t['name']}")
        elif "playlist" in url:
            m = re.search(r"playlist/([A-Za-z0-9]+)", url)
            if m:
                res = sp.playlist_tracks(m.group(1), limit=100)
                while res:
                    for item in res["items"]:
                        t = item.get("track")
                        if t:
                            tracks.append(f"{t['artists'][0]['name']} {t['name']}")
                    res = sp.next(res) if res["next"] else None
        elif "album" in url:
            m = re.search(r"album/([A-Za-z0-9]+)", url)
            if m:
                res = sp.album_tracks(m.group(1))
                for t in res["items"]:
                    tracks.append(f"{t['artists'][0]['name']} {t['name']}")
    except Exception as e:
        print(f"[Spotify] {e}")
    return tracks


def track_embed(track: dict, st: GuildState,
                title: str = "🎵 Now Playing") -> discord.Embed:
    emb = discord.Embed(
        title=title,
        description=f"**[{track['title']}]({track.get('webpage_url', '#')})**",
        color=0x9B59B6,
    )
    if track.get("thumbnail"):
        emb.set_thumbnail(url=track["thumbnail"])
    emb.add_field(name="⏱ Duration",  value=fmt_dur(track.get("duration")), inline=True)
    emb.add_field(name="📺 Source",    value=track.get("uploader", "Unknown"),  inline=True)
    emb.add_field(name="🔊 Volume",   value=f"{st.volume}%",  inline=True)
    emb.add_field(name="🔁 Loop",     value=loop_badge(st.loop_mode), inline=True)
    emb.add_field(name="✨ Effects",  value=effect_badge(st), inline=True)
    emb.add_field(name="📋 Queue",    value=f"{len(st.queue)} tracks", inline=True)
    return emb


# ══════════════════════════════════════════════════════════
#  ЯДРО ВОСПРОИЗВЕДЕНИЯ
# ══════════════════════════════════════════════════════════
async def play_next(guild_id: int):
    st = state(guild_id)

    if not st.voice or not st.voice.is_connected():
        st.playing = False
        return

    # Управление очередью по режиму loop
    if st.loop_mode == "queue" and st.current:
        st.queue.append(st.current)

    if not st.queue and st.loop_mode != "one":
        st.current = None
        st.playing = False
        if st.text_ch:
            await st.text_ch.send(embed=discord.Embed(
                description="✅ Очередь закончилась! Добавьте ещё треков.",
                color=0x2ECC71,
            ))
        return

    track = st.current if st.loop_mode == "one" and st.current else st.queue.popleft()
    st.current     = track
    st.playing     = True
    st.start_time  = time.time()
    st.total_paused = 0.0
    st.paused_at   = None

    ffmpeg_opts = build_ffmpeg_opts(
        bass=st.bass, nightcore=st.nightcore, speed=st.speed,
        volume=st.volume, treble=st.treble, vaporwave=st.vaporwave,
        echo=st.echo,
    )

    try:
        url = track.get("url") or track.get("webpage_url")
        source = discord.FFmpegPCMAudio(url, **ffmpeg_opts)

        def _after(err):
            if err:
                print(f"[Player error] {err}")
            asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

        st.voice.play(source, after=_after)

        if st.text_ch:
            await st.text_ch.send(embed=track_embed(track, st))
    except Exception as e:
        print(f"[play_next] {e}")
        if st.text_ch:
            await st.text_ch.send(
                embed=discord.Embed(description=f"❌ Ошибка воспроизведения: `{e}`", color=0xE74C3C)
            )
        await asyncio.sleep(1)
        await play_next(guild_id)


async def restart_playback(guild_id: int):
    """Перезапускает текущий трек (нужно при смене эффектов)."""
    st = state(guild_id)
    if st.current and st.voice:
        st.queue.appendleft(st.current)
        st.current = None
        st.voice.stop()          # after → play_next


# ══════════════════════════════════════════════════════════
#  BOT INIT
# ══════════════════════════════════════════════════════════
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states    = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


# ══════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════
@bot.event
async def on_ready():
    print(f"✅  {bot.user} запущен | {len(bot.guilds)} сервер(ов)")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name=f"{PREFIX}help | Music Bot"
    ))
    auto_leave_check.start()


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=discord.Embed(
            description=f"❌ Не хватает аргумента: `{error.param.name}`\n"
                        f"Используй `{PREFIX}help` для справки.",
            color=0xE74C3C,
        ))
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(embed=discord.Embed(
            description=f"❌ Неверный аргумент: `{error}`", color=0xE74C3C
        ))
        return
    raise error


@bot.event
async def on_voice_state_update(member: discord.Member,
                                before: discord.VoiceState,
                                after: discord.VoiceState):
    if member.bot:
        return
    for guild in bot.guilds:
        vc = guild.voice_client
        if vc and before.channel == vc.channel:
            humans = [m for m in vc.channel.members if not m.bot]
            st = state(guild.id)
            if not humans:
                st.alone_since = time.time()
            else:
                st.alone_since = None


@tasks.loop(seconds=30)
async def auto_leave_check():
    for guild in bot.guilds:
        vc = guild.voice_client
        st = state(guild.id)
        if vc and st.alone_since:
            if time.time() - st.alone_since >= AUTO_LEAVE_DELAY:
                st.queue.clear()
                st.current = None
                st.playing = False
                await vc.disconnect()
                st.voice = None
                st.alone_since = None
                if st.text_ch:
                    await st.text_ch.send(embed=discord.Embed(
                        description="👋 Вышел из голосового канала (все ушли).",
                        color=0x95A5A6,
                    ))


# ══════════════════════════════════════════════════════════
#  ХЕЛПЕР: проверки
# ══════════════════════════════════════════════════════════
async def ensure_voice(ctx: commands.Context) -> bool:
    if not ctx.author.voice:
        await ctx.send(embed=discord.Embed(
            description="❌ Зайди в голосовой канал!", color=0xE74C3C
        ))
        return False
    st = state(ctx.guild.id)
    if not ctx.voice_client:
        vc = await ctx.author.voice.channel.connect()
        st.voice = vc
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)
        st.voice = ctx.voice_client
    else:
        st.voice = ctx.voice_client
    st.text_ch = ctx.channel
    return True


def check_playing(ctx: commands.Context) -> bool:
    return bool(ctx.voice_client and ctx.voice_client.is_playing())


# ══════════════════════════════════════════════════════════
#  КОМАНДЫ — ВОСПРОИЗВЕДЕНИЕ
# ══════════════════════════════════════════════════════════
@bot.command(name="play", aliases=["p", "add"])
async def cmd_play(ctx: commands.Context, *, query: str):
    """▶️ Воспроизвести трек/плейлист (YouTube, SoundCloud, Spotify, и др.)"""
    if not await ensure_voice(ctx):
        return
    st = state(ctx.guild.id)

    async with ctx.typing():
        added = 0

        # ── Spotify ──────────────────────────────────────
        if "spotify.com" in query:
            if not sp:
                await ctx.send(embed=discord.Embed(
                    description="❌ Укажи SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET в .env",
                    color=0xE74C3C,
                ))
                return
            names = await fetch_spotify(query)
            if not names:
                await ctx.send(embed=discord.Embed(
                    description="❌ Не удалось загрузить треки из Spotify", color=0xE74C3C
                ))
                return
            msg = await ctx.send(embed=discord.Embed(
                description=f"🔍 Загружаю **{len(names)}** треков из Spotify…",
                color=0x1DB954,
            ))
            for name in names[:100]:
                info = await fetch_info(name)
                if info:
                    entry = info["entries"][0] if "entries" in info else info
                    if entry:
                        st.queue.append(entry)
                        added += 1
            await msg.edit(embed=discord.Embed(
                description=f"✅ Добавлено **{added}** треков из Spotify!",
                color=0x1DB954,
            ))

        # ── Плейлист ─────────────────────────────────────
        elif "playlist" in query or "list=" in query:
            info = await fetch_info(query, playlist=True)
            if info and "entries" in info:
                for entry in info["entries"]:
                    if entry:
                        st.queue.append(entry)
                        added += 1
            await ctx.send(embed=discord.Embed(
                description=f"✅ Добавлено **{added}** треков из плейлиста!",
                color=0x2ECC71,
            ))

        # ── Один трек ────────────────────────────────────
        else:
            info = await fetch_info(query)
            if not info:
                await ctx.send(embed=discord.Embed(
                    description="❌ Ничего не найдено!", color=0xE74C3C
                ))
                return
            entry = info["entries"][0] if "entries" in info else info
            if not entry:
                await ctx.send(embed=discord.Embed(
                    description="❌ Ничего не найдено!", color=0xE74C3C
                ))
                return
            st.queue.append(entry)
            added = 1
            if st.playing:
                emb = discord.Embed(
                    title="➕ Добавлено в очередь",
                    description=f"**[{entry['title']}]({entry.get('webpage_url','#')})**",
                    color=0x3498DB,
                )
                if entry.get("thumbnail"):
                    emb.set_thumbnail(url=entry["thumbnail"])
                emb.add_field(name="⏱ Duration", value=fmt_dur(entry.get("duration")), inline=True)
                emb.add_field(name="📋 Position", value=f"#{len(st.queue)}", inline=True)
                await ctx.send(embed=emb)

    if not st.playing and added:
        await play_next(ctx.guild.id)


@bot.command(name="playtop", aliases=["pt", "addtop"])
async def cmd_playtop(ctx: commands.Context, *, query: str):
    """⬆️ Добавить трек в начало очереди"""
    if not await ensure_voice(ctx):
        return
    st = state(ctx.guild.id)
    async with ctx.typing():
        info = await fetch_info(query)
        if not info:
            return await ctx.send(embed=discord.Embed(description="❌ Не найдено!", color=0xE74C3C))
        entry = info["entries"][0] if "entries" in info else info
        if not entry:
            return await ctx.send(embed=discord.Embed(description="❌ Не найдено!", color=0xE74C3C))
        st.queue.appendleft(entry)
    if st.playing:
        await ctx.send(embed=discord.Embed(
            description=f"⬆️ **{entry['title']}** добавлен в начало очереди!",
            color=0x2ECC71,
        ))
    else:
        await play_next(ctx.guild.id)


@bot.command(name="search", aliases=["find"])
async def cmd_search(ctx: commands.Context, *, query: str):
    """🔍 Найти трек и выбрать из 5 результатов"""
    if not await ensure_voice(ctx):
        return
    async with ctx.typing():
        info = await fetch_info(f"ytsearch5:{query}", playlist=True)
    if not info or "entries" not in info:
        return await ctx.send(embed=discord.Embed(description="❌ Ничего не найдено!", color=0xE74C3C))

    entries = [e for e in info["entries"] if e][:5]
    if not entries:
        return await ctx.send(embed=discord.Embed(description="❌ Ничего не найдено!", color=0xE74C3C))

    emb = discord.Embed(title=f"🔍 Результаты: {query}", color=0x3498DB)
    for i, e in enumerate(entries, 1):
        emb.add_field(
            name=f"{i}. {e['title'][:60]}",
            value=f"⏱ {fmt_dur(e.get('duration'))} • 📺 {e.get('uploader','?')}",
            inline=False,
        )
    emb.set_footer(text="Ответь числом 1–5 или 'отмена' в течение 30 сек.")
    await ctx.send(embed=emb)

    def check(m):
        return (m.author == ctx.author and m.channel == ctx.channel
                and (m.content.lower() in ("отмена", "cancel")
                     or (m.content.isdigit() and 1 <= int(m.content) <= len(entries))))
    try:
        reply = await bot.wait_for("message", timeout=30.0, check=check)
    except asyncio.TimeoutError:
        return await ctx.send(embed=discord.Embed(description="⏰ Время вышло!", color=0xF39C12))

    if reply.content.lower() in ("отмена", "cancel"):
        return await ctx.send(embed=discord.Embed(description="❌ Поиск отменён.", color=0xE74C3C))

    entry = entries[int(reply.content) - 1]
    st = state(ctx.guild.id)
    st.queue.append(entry)
    if not st.playing:
        await play_next(ctx.guild.id)
    else:
        await ctx.send(embed=discord.Embed(
            title="➕ Добавлено в очередь",
            description=f"**{entry['title']}**",
            color=0x3498DB,
        ))


@bot.command(name="skip", aliases=["s", "next"])
async def cmd_skip(ctx: commands.Context, count: int = 1):
    """⏭ Пропустить текущий (или N) треков"""
    st = state(ctx.guild.id)
    if not check_playing(ctx):
        return await ctx.send(embed=discord.Embed(description="❌ Ничего не играет!", color=0xE74C3C))
    skipped = 1
    for _ in range(count - 1):
        if st.queue:
            st.queue.popleft()
            skipped += 1
    ctx.voice_client.stop()
    await ctx.send(embed=discord.Embed(
        description=f"⏭ Пропущено **{skipped}** трек(ов)!",
        color=0x3498DB,
    ))


@bot.command(name="stop")
async def cmd_stop(ctx: commands.Context):
    """⏹ Остановить и выйти"""
    st = state(ctx.guild.id)
    st.queue.clear()
    st.current = None
    st.playing = False
    st.loop_mode = "off"
    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        st.voice = None
    await ctx.send(embed=discord.Embed(description="⏹ Остановлено и отключено!", color=0xE74C3C))


@bot.command(name="pause")
async def cmd_pause(ctx: commands.Context):
    """⏸ Поставить на паузу"""
    st = state(ctx.guild.id)
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        st.paused_at = time.time()
        await ctx.send(embed=discord.Embed(description="⏸ Пауза!", color=0xF39C12))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Ничего не играет!", color=0xE74C3C))


@bot.command(name="resume", aliases=["r", "continue"])
async def cmd_resume(ctx: commands.Context):
    """▶️ Продолжить воспроизведение"""
    st = state(ctx.guild.id)
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        if st.paused_at:
            st.total_paused += time.time() - st.paused_at
            st.paused_at = None
        await ctx.send(embed=discord.Embed(description="▶️ Продолжаю!", color=0x2ECC71))
    else:
        await ctx.send(embed=discord.Embed(description="❌ Нечего возобновлять!", color=0xE74C3C))


@bot.command(name="disconnect", aliases=["dc", "leave", "bye"])
async def cmd_disconnect(ctx: commands.Context):
    """👋 Выйти из голосового канала"""
    st = state(ctx.guild.id)
    st.queue.clear(); st.current = None; st.playing = False
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        st.voice = None
    await ctx.send(embed=discord.Embed(description="👋 Пока-пока!", color=0x95A5A6))


# ══════════════════════════════════════════════════════════
#  КОМАНДЫ — ОЧЕРЕДЬ
# ══════════════════════════════════════════════════════════
@bot.command(name="queue", aliases=["q", "list"])
async def cmd_queue(ctx: commands.Context, page: int = 1):
    """📋 Показать очередь"""
    st = state(ctx.guild.id)
    if not st.current and not st.queue:
        return await ctx.send(embed=discord.Embed(description="📭 Очередь пуста!", color=0xF39C12))

    PER_PAGE = 10
    total = len(st.queue)
    pages = max(1, math.ceil(total / PER_PAGE))
    page = max(1, min(page, pages))
    start = (page - 1) * PER_PAGE

    emb = discord.Embed(title="📋 Очередь воспроизведения", color=0x9B59B6)

    if st.current:
        elapsed = 0.0
        if st.start_time:
            elapsed = time.time() - st.start_time - st.total_paused
        dur = st.current.get("duration", 0)
        bar = progress_bar(elapsed, dur)
        emb.add_field(
            name="▶️ Сейчас играет",
            value=f"**{st.current['title']}**\n{fmt_dur(elapsed)} {bar} {fmt_dur(dur)}",
            inline=False,
        )

    slice_ = list(st.queue)[start:start + PER_PAGE]
    if slice_:
        lines = []
        for i, t in enumerate(slice_, start=start + 1):
            lines.append(f"`{i}.` **{t['title'][:55]}** — {fmt_dur(t.get('duration'))}")
        emb.add_field(name=f"Далее (стр. {page}/{pages})", value="\n".join(lines), inline=False)

    total_dur = sum(t.get("duration", 0) or 0 for t in st.queue)
    emb.set_footer(
        text=f"Треков: {total} • Общее время: {fmt_dur(total_dur)} • "
             f"循環: {loop_badge(st.loop_mode)}"
    )
    await ctx.send(embed=emb)


@bot.command(name="nowplaying", aliases=["np", "current"])
async def cmd_np(ctx: commands.Context):
    """🎵 Текущий трек с прогрессом"""
    st = state(ctx.guild.id)
    if not st.current:
        return await ctx.send(embed=discord.Embed(description="❌ Ничего не играет!", color=0xE74C3C))

    elapsed = 0.0
    if st.start_time:
        elapsed = time.time() - st.start_time - st.total_paused
        if st.paused_at:
            elapsed -= time.time() - st.paused_at

    dur = st.current.get("duration", 0)
    emb = discord.Embed(
        title="🎵 Сейчас играет",
        description=f"**[{st.current['title']}]({st.current.get('webpage_url','#')})**",
        color=0x9B59B6,
    )
    if st.current.get("thumbnail"):
        emb.set_thumbnail(url=st.current["thumbnail"])
    if dur:
        bar = progress_bar(elapsed, dur)
        emb.add_field(
            name="⏱ Прогресс",
            value=f"`{fmt_dur(elapsed)}` {bar} `{fmt_dur(dur)}`",
            inline=False,
        )
    emb.add_field(name="🔊 Громкость",  value=f"{st.volume}%",         inline=True)
    emb.add_field(name="🔁 Лупинг",    value=loop_badge(st.loop_mode), inline=True)
    emb.add_field(name="✨ Эффекты",   value=effect_badge(st),         inline=True)
    emb.add_field(name="📺 Источник",  value=st.current.get("uploader","?"), inline=True)
    emb.add_field(name="📋 В очереди", value=str(len(st.queue)),       inline=True)
    await ctx.send(embed=emb)


@bot.command(name="shuffle", aliases=["sh", "mix"])
async def cmd_shuffle(ctx: commands.Context):
    """🔀 Перемешать очередь"""
    st = state(ctx.guild.id)
    if not st.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Очередь пуста!", color=0xE74C3C))
    lst = list(st.queue)
    random.shuffle(lst)
    st.queue = deque(lst)
    await ctx.send(embed=discord.Embed(description="🔀 Очередь перемешана!", color=0x9B59B6))


@bot.command(name="clear", aliases=["clq"])
async def cmd_clear(ctx: commands.Context):
    """🧹 Очистить очередь"""
    st = state(ctx.guild.id)
    count = len(st.queue)
    st.queue.clear()
    await ctx.send(embed=discord.Embed(
        description=f"🧹 Очередь очищена ({count} треков удалено)!", color=0xF39C12
    ))


@bot.command(name="remove", aliases=["rm", "del"])
async def cmd_remove(ctx: commands.Context, index: int):
    """🗑 Удалить трек из очереди по позиции"""
    st = state(ctx.guild.id)
    if not st.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Очередь пуста!", color=0xE74C3C))
    if not 1 <= index <= len(st.queue):
        return await ctx.send(embed=discord.Embed(
            description=f"❌ Позиция должна быть от 1 до {len(st.queue)}", color=0xE74C3C
        ))
    lst = list(st.queue)
    removed = lst.pop(index - 1)
    st.queue = deque(lst)
    await ctx.send(embed=discord.Embed(
        description=f"🗑 Удалено: **{removed['title']}**", color=0xF39C12
    ))


@bot.command(name="move", aliases=["mv"])
async def cmd_move(ctx: commands.Context, from_pos: int, to_pos: int):
    """🔄 Переместить трек в очереди"""
    st = state(ctx.guild.id)
    n = len(st.queue)
    if not st.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Очередь пуста!", color=0xE74C3C))
    if not (1 <= from_pos <= n and 1 <= to_pos <= n):
        return await ctx.send(embed=discord.Embed(
            description=f"❌ Позиции должны быть от 1 до {n}", color=0xE74C3C
        ))
    lst = list(st.queue)
    track = lst.pop(from_pos - 1)
    lst.insert(to_pos - 1, track)
    st.queue = deque(lst)
    await ctx.send(embed=discord.Embed(
        description=f"✅ **{track['title']}** → позиция **{to_pos}**", color=0x2ECC71
    ))


@bot.command(name="skipto", aliases=["goto"])
async def cmd_skipto(ctx: commands.Context, index: int):
    """⏩ Перейти к треку по номеру в очереди"""
    st = state(ctx.guild.id)
    if not st.queue:
        return await ctx.send(embed=discord.Embed(description="❌ Очередь пуста!", color=0xE74C3C))
    if not 1 <= index <= len(st.queue):
        return await ctx.send(embed=discord.Embed(
            description=f"❌ Позиция от 1 до {len(st.queue)}", color=0xE74C3C
        ))
    for _ in range(index - 1):
        st.queue.popleft()
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
    await ctx.send(embed=discord.Embed(
        description=f"⏩ Перемотка к треку #{index}!", color=0x3498DB
    ))


# ══════════════════════════════════════════════════════════
#  КОМАНДЫ — LOOP
# ══════════════════════════════════════════════════════════
@bot.command(name="loop", aliases=["l", "repeat"])
async def cmd_loop(ctx: commands.Context, mode: str = None):
    """🔁 Режим повтора: off / one / queue"""
    st = state(ctx.guild.id)
    modes = ["off", "one", "queue"]
    if mode is None:
        idx = modes.index(st.loop_mode)
        st.loop_mode = modes[(idx + 1) % len(modes)]
    elif mode.lower() in modes:
        st.loop_mode = mode.lower()
    else:
        return await ctx.send(embed=discord.Embed(
            description="❌ Режимы: `off`, `one`, `queue`", color=0xE74C3C
        ))
    await ctx.send(embed=discord.Embed(
        description=f"Режим повтора: **{loop_badge(st.loop_mode)}**", color=0x9B59B6
    ))


# ══════════════════════════════════════════════════════════
#  КОМАНДЫ — ЭФФЕКТЫ / AUDIO FILTERS
# ══════════════════════════════════════════════════════════
async def _toggle_effect(ctx, attr: str, label: str, emoji: str):
    st = state(ctx.guild.id)
    current = getattr(st, attr)
    setattr(st, attr, not current)
    new = getattr(st, attr)
    await ctx.send(embed=discord.Embed(
        description=f"{emoji} {label}: **{'✅ ВКЛ' if new else '❌ ВЫКЛ'}**",
        color=0x9B59B6 if new else 0x95A5A6,
    ))
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await restart_playback(ctx.guild.id)


@bot.command(name="bassboost", aliases=["bb", "bass"])
async def cmd_bass(ctx: commands.Context):
    """🎸 Включить/выключить Bass Boost"""
    await _toggle_effect(ctx, "bass", "Bass Boost", "🎸")


@bot.command(name="nightcore", aliases=["nc"])
async def cmd_nightcore(ctx: commands.Context):
    """🌙 Включить/выключить Nightcore"""
    st = state(ctx.guild.id)
    # Nightcore и vaporwave взаимоисключающие
    if not st.nightcore and st.vaporwave:
        st.vaporwave = False
    await _toggle_effect(ctx, "nightcore", "Nightcore", "🌙")


@bot.command(name="vaporwave", aliases=["vapor", "vw"])
async def cmd_vaporwave(ctx: commands.Context):
    """🌊 Включить/выключить Vaporwave (замедление)"""
    st = state(ctx.guild.id)
    if not st.vaporwave and st.nightcore:
        st.nightcore = False
    await _toggle_effect(ctx, "vaporwave", "Vaporwave", "🌊")


@bot.command(name="treble", aliases=["trebleboost", "tb"])
async def cmd_treble(ctx: commands.Context):
    """🎶 Включить/выключить Treble Boost"""
    await _toggle_effect(ctx, "treble", "Treble Boost", "🎶")


@bot.command(name="echo")
async def cmd_echo(ctx: commands.Context):
    """🌀 Включить/выключить Echo"""
    await _toggle_effect(ctx, "echo", "Echo", "🌀")


@bot.command(name="speed", aliases=["sp"])
async def cmd_speed(ctx: commands.Context, value: float = None):
    """⚡ Скорость воспроизведения (0.5 – 2.0). Без аргумента — текущее значение."""
    st = state(ctx.guild.id)
    if value is None:
        return await ctx.send(embed=discord.Embed(
            description=f"⚡ Текущая скорость: **{st.speed}x**", color=0x3498DB
        ))
    if not 0.5 <= value <= 2.0:
        return await ctx.send(embed=discord.Embed(
            description="❌ Скорость: от **0.5** до **2.0**", color=0xE74C3C
        ))
    st.speed = round(value, 2)
    await ctx.send(embed=discord.Embed(
        description=f"⚡ Скорость: **{st.speed}x**", color=0x9B59B6
    ))
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await restart_playback(ctx.guild.id)


@bot.command(name="volume", aliases=["vol", "v"])
async def cmd_volume(ctx: commands.Context, value: int = None):
    """🔊 Громкость 0–200%. Без аргумента — текущее значение."""
    st = state(ctx.guild.id)
    if value is None:
        bar = "█" * (st.volume // 10) + "░" * (20 - st.volume // 10)
        return await ctx.send(embed=discord.Embed(
            description=f"🔊 Громкость: **{st.volume}%**\n`{bar}`", color=0x3498DB
        ))
    if not 0 <= value <= 200:
        return await ctx.send(embed=discord.Embed(
            description="❌ Громкость: от **0** до **200**", color=0xE74C3C
        ))
    st.volume = value
    await ctx.send(embed=discord.Embed(
        description=f"🔊 Громкость установлена: **{value}%**", color=0x2ECC71
    ))
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await restart_playback(ctx.guild.id)


@bot.command(name="resetfx", aliases=["rfx", "nofx"])
async def cmd_resetfx(ctx: commands.Context):
    """♻️ Сбросить все аудиоэффекты"""
    st = state(ctx.guild.id)
    st.bass = False; st.nightcore = False; st.treble = False
    st.echo = False; st.vaporwave = False; st.speed = 1.0
    await ctx.send(embed=discord.Embed(description="♻️ Все эффекты сброшены!", color=0x2ECC71))
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        await restart_playback(ctx.guild.id)


@bot.command(name="effects", aliases=["fx", "filters"])
async def cmd_effects(ctx: commands.Context):
    """✨ Показать текущие эффекты"""
    st = state(ctx.guild.id)
    emb = discord.Embed(title="✨ Текущие аудиоэффекты", color=0x9B59B6)
    emb.add_field(name="🎸 Bass Boost",   value="✅ ВКЛ" if st.bass       else "❌ ВЫКЛ", inline=True)
    emb.add_field(name="🌙 Nightcore",    value="✅ ВКЛ" if st.nightcore  else "❌ ВЫКЛ", inline=True)
    emb.add_field(name="🌊 Vaporwave",    value="✅ ВКЛ" if st.vaporwave  else "❌ ВЫКЛ", inline=True)
    emb.add_field(name="🎶 Treble Boost", value="✅ ВКЛ" if st.treble     else "❌ ВЫКЛ", inline=True)
    emb.add_field(name="🌀 Echo",         value="✅ ВКЛ" if st.echo       else "❌ ВЫКЛ", inline=True)
    emb.add_field(name="⚡ Скорость",     value=f"{st.speed}x",                           inline=True)
    emb.add_field(name="🔊 Громкость",    value=f"{st.volume}%",                          inline=True)
    emb.add_field(name="🔁 Лупинг",      value=loop_badge(st.loop_mode),                 inline=True)
    await ctx.send(embed=emb)


# ══════════════════════════════════════════════════════════
#  КОМАНДЫ — ПРОЧЕЕ
# ══════════════════════════════════════════════════════════
@bot.command(name="join")
async def cmd_join(ctx: commands.Context):
    """🎤 Зайти в твой голосовой канал"""
    await ensure_voice(ctx)
    await ctx.send(embed=discord.Embed(
        description=f"🎤 Зашёл в **{ctx.author.voice.channel.name}**!",
        color=0x2ECC71,
    ))


@bot.command(name="ping")
async def cmd_ping(ctx: commands.Context):
    """🏓 Задержка бота"""
    await ctx.send(embed=discord.Embed(
        description=f"🏓 Pong! `{round(bot.latency * 1000)}ms`",
        color=0x2ECC71,
    ))


@bot.command(name="help", aliases=["h", "commands", "cmds"])
async def cmd_help(ctx: commands.Context):
    """📖 Список команд"""
    p = PREFIX
    emb = discord.Embed(
        title="🎵 Music Bot — Список команд",
        description=f"Префикс: `{p}` | Поддержка: YouTube, SoundCloud, Spotify и др.",
        color=0x9B59B6,
    )

    emb.add_field(name="▶️ Воспроизведение", value=(
        f"`{p}play <запрос/ссылка>` — Играть трек/плейлист\n"
        f"`{p}playtop <запрос>` — Добавить в начало очереди\n"
        f"`{p}search <запрос>` — Поиск с выбором из 5 результатов\n"
        f"`{p}skip [N]` — Пропустить N треков\n"
        f"`{p}skipto <N>` — Перейти к треку #N\n"
        f"`{p}pause` / `{p}resume` — Пауза / Продолжить\n"
        f"`{p}stop` — Остановить и выйти\n"
        f"`{p}join` / `{p}disconnect` — Войти / Выйти"
    ), inline=False)

    emb.add_field(name="📋 Очередь", value=(
        f"`{p}queue [стр.]` — Показать очередь\n"
        f"`{p}nowplaying` — Текущий трек + прогресс\n"
        f"`{p}shuffle` — Перемешать\n"
        f"`{p}clear` — Очистить очередь\n"
        f"`{p}remove <N>` — Удалить трек #N\n"
        f"`{p}move <откуда> <куда>` — Переместить трек\n"
        f"`{p}loop [off/one/queue]` — Режим повтора"
    ), inline=False)

    emb.add_field(name="✨ Эффекты и настройки", value=(
        f"`{p}bassboost` — 🎸 Bass Boost\n"
        f"`{p}nightcore` — 🌙 Nightcore (+скорость +тон)\n"
        f"`{p}vaporwave` — 🌊 Vaporwave (замедление)\n"
        f"`{p}treble` — 🎶 Treble Boost (высокие частоты)\n"
        f"`{p}echo` — 🌀 Echo (эхо)\n"
        f"`{p}speed [0.5–2.0]` — ⚡ Скорость воспроизведения\n"
        f"`{p}volume [0–200]` — 🔊 Громкость\n"
        f"`{p}effects` — Показать все эффекты\n"
        f"`{p}resetfx` — ♻️ Сбросить все эффекты"
    ), inline=False)

    emb.add_field(name="ℹ️ Прочее", value=(
        f"`{p}ping` — Задержка бота\n"
        f"`{p}help` — Этот список"
    ), inline=False)

    emb.set_footer(text="💡 Ссылки: YouTube, SoundCloud, Spotify (трек/плейлист/альбом), Bandcamp и др.")
    await ctx.send(embed=emb)


# ══════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
