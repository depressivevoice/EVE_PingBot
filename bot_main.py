import os
import re
import json
import logging
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord import AllowedMentions
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set.")
if not TG_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")


# =========================================================
# Telegram routing (chat_id + topic_id per category)
# =========================================================

TG_CHAT_ID_DEFAULT = os.getenv("TELEGRAM_CHAT_ID_DEFAULT") or os.getenv("TELEGRAM_CHAT_ID")
if not TG_CHAT_ID_DEFAULT:
    raise RuntimeError("TELEGRAM_CHAT_ID_DEFAULT (or TELEGRAM_CHAT_ID) is not set.")

TG_TOPIC_ID_DEFAULT = os.getenv("TELEGRAM_TOPIC_ID_DEFAULT") or os.getenv("TELEGRAM_TOPIC_ID")
if TG_TOPIC_ID_DEFAULT and str(TG_TOPIC_ID_DEFAULT).strip():
    try:
        TG_TOPIC_ID_DEFAULT = int(str(TG_TOPIC_ID_DEFAULT).strip())
    except ValueError:
        raise RuntimeError("TELEGRAM_TOPIC_ID_DEFAULT / TELEGRAM_TOPIC_ID must be an integer if set.")
else:
    TG_TOPIC_ID_DEFAULT = None


def pick_tg_chat_id(category: str) -> str:
    """
    category: STRATOP / PREPING / BREAKING_NEWS / CORP_ACTIVITY
    """
    key = f"TELEGRAM_CHAT_ID_{category.upper()}"
    v = os.getenv(key)
    if v and str(v).strip():
        return str(v).strip()

    fallback = os.getenv("TELEGRAM_CHAT_ID_PINGS")
    if fallback and str(fallback).strip():
        return str(fallback).strip()

    return str(TG_CHAT_ID_DEFAULT).strip()


def pick_tg_topic_id(category: str) -> int | None:
    key = f"TELEGRAM_TOPIC_ID_{category.upper()}"
    v = os.getenv(key)
    if v and str(v).strip():
        try:
            return int(str(v).strip())
        except ValueError:
            raise RuntimeError(f"{key} must be an integer if set.")

    fallback = os.getenv("TELEGRAM_TOPIC_ID_PINGS")
    if fallback and str(fallback).strip():
        try:
            return int(str(fallback).strip())
        except ValueError:
            raise RuntimeError("TELEGRAM_TOPIC_ID_PINGS must be an integer if set.")

    return TG_TOPIC_ID_DEFAULT


# =========================================================
# Last ping per Discord channel
# =========================================================

LAST_PING_FILE = "last_ping.json"


def load_last_ping() -> dict:
    try:
        with open(LAST_PING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_last_ping(
    channel_id: int,
    discord_message_id: int,
    tg_chat_id: str | None,
    tg_topic_id: int | None,
    tg_message_id: int | None,
    tg_text: str | None,
) -> None:
    data = load_last_ping()
    data[str(channel_id)] = {
        "discord_message_id": discord_message_id,
        "tg_chat_id": tg_chat_id,
        "tg_topic_id": tg_topic_id,
        "tg_message_id": tg_message_id,
        "tg_text": tg_text,
    }
    with open(LAST_PING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


# =========================================================
# Helpers
# =========================================================

def escape_md_v2(text: str) -> str:
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


def parse_et_date_time(date_s: str, time_s: str) -> datetime:
    date_s = date_s.strip()
    time_s = time_s.strip()

    tm = re.fullmatch(r"(\d{2}):(\d{2})", time_s)
    if not tm:
        raise ValueError("Bad time format. Use 'HH:MM' ET.")
    hour, minute = map(int, tm.groups())

    # date with year
    try:
        d = datetime.strptime(date_s, "%d.%m.%Y")
        return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        pass

    # date without year
    dm = re.fullmatch(r"(\d{2})\.(\d{2})", date_s)
    if not dm:
        raise ValueError("Bad date format. Use 'DD.MM.YYYY' or 'DD.MM' ET.")
    day, month = map(int, dm.groups())

    now = datetime.now(timezone.utc)
    try:
        dt = datetime(now.year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("Invalid date/time values.")

    if dt < now:
        try:
            dt = datetime(now.year + 1, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date/time values.")
    return dt


def discord_time(ts: int) -> str:
    return f"<t:{ts}:F> • ⏳ <t:{ts}:R>"


async def send_to_telegram(chat_id: str, topic_id: int | None, text: str) -> int | None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    if topic_id is not None:
        payload["message_thread_id"] = topic_id

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as r:
            data = await r.json()
            if not data.get("ok"):
                raise RuntimeError(data)
            return data["result"]["message_id"]


def tg_apply_status(base_text: str, status: str) -> str:
    """
    Append or replace a trailing status block:
      \n\n*СТАТУС:* ...
    """
    cleaned = re.sub(r"\n\*СТАТУС:\*.*$", "", base_text, flags=re.S)
    return cleaned + f"\n\n*СТАТУС:* {escape_md_v2(status)}"


def tg_replace_link(base_text: str, new_url: str) -> str:
    """Remove any existing TG link block and append a fresh one."""
    # Remove any existing "Ссылка на пинг" markdown link (and surrounding blank lines).
    cleaned = re.sub(r"\n*\[Ссылка на пинг\]\([^\)]*\)\s*", "", base_text, flags=re.S)
    cleaned = cleaned.rstrip()
    return cleaned + f"\n\n[Ссылка на пинг]({escape_md_v2(new_url)})"


def embed_without_link_field(src: discord.Embed) -> discord.Embed:
    """Clone embed and remove the 'Ссылка' field (so reping can inject a fresh link)."""
    d = src.to_dict()
    fields = d.get("fields", [])
    d["fields"] = [f for f in fields if f.get("name") != "Ссылка"]
    return discord.Embed.from_dict(d)


async def post_ping(
    interaction: discord.Interaction,
    *,
    category: str,
    embed: discord.Embed,
    tg_text: str | None,
) -> None:
    if interaction.channel is None:
        await interaction.followup.send("Не вижу канал для отправки сообщения.", ephemeral=True)
        return

    # Always normal channel message (not webhook), to keep unread markers reliable.
    msg = await interaction.channel.send(
        content="@everyone",
        embed=embed,
        allowed_mentions=AllowedMentions(everyone=True),
    )
    jump_url = msg.jump_url
    # Safe edit: only embed (do not touch content/mentions).
    embed.add_field(name="Ссылка", value=f"[Ссылка на этот пинг]({jump_url})", inline=False)
    await msg.edit(embed=embed)

    tg_chat_id = None
    tg_topic_id = None
    tg_message_id = None
    final_tg_text = None
    if tg_text is not None:
        tg_chat_id = pick_tg_chat_id(category)
        tg_topic_id = pick_tg_topic_id(category)
        tg_text_with_link = tg_text + f"\n\n[Ссылка на пинг]({escape_md_v2(jump_url)})"

        tg_message_id = await send_to_telegram(tg_chat_id, tg_topic_id, tg_text_with_link)
        final_tg_text = tg_text_with_link

    save_last_ping(
        msg.channel.id,
        msg.id,
        tg_chat_id,
        tg_topic_id,
        tg_message_id,
        final_tg_text,
    )


# =========================================================
# Discord bot
# =========================================================

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

COMMS_CHOICES = [
    app_commands.Choice(name="Mumble CN", value="Mumble CN"),
    app_commands.Choice(name="Mumble EU", value="Mumble EU"),
    app_commands.Choice(name="Discord", value="Discord"),
    app_commands.Choice(name="False", value="False"),
]


@client.event
async def on_ready():
    await tree.sync()
    logging.info(f"Logged in as {client.user} (ID: {client.user.id})")


# =========================================================
# Commands
# =========================================================

@tree.command(name="stratop", description="STRAT-OP (Forming) — без даты/времени")
@app_commands.describe(
    formup="Место сбора",
    doctrine="Доктрина",
    fc="FC",
    notes="Примечание (опционально)",
    comms="Коммуникации",
    room="Название комнаты (если Comms выбрано)",
)
@app_commands.choices(comms=COMMS_CHOICES)
async def stratop_forming(
    interaction: discord.Interaction,
    formup: str = "TBD",
    doctrine: str = "TBD",
    fc: str = "TBD",
    notes: str = "",
    comms: str = "False",
    room: str = "",
):
    await interaction.response.defer()

    label = "STRAT-OP Флот СОБИРАЕТСЯ!"
    embed = discord.Embed(title=f"🚨 {label}", color=discord.Color.red())
    embed.add_field(name="Место сбора", value=formup, inline=False)
    embed.add_field(name="Доктрина", value=doctrine, inline=False)
    embed.add_field(name="FC", value=fc, inline=True)

    if comms != "False":
        embed.add_field(name="Comms", value=comms, inline=True)
        if room.strip():
            embed.add_field(name="Комната", value=room, inline=True)

    if notes.strip():
        embed.add_field(name="Примечание", value=notes, inline=False)

    tg_lines = [
        f"*🚨 {escape_md_v2(label)}*",
        "",
        "*Место сбора*",
        escape_md_v2(formup),
        "*Доктрина*",
        escape_md_v2(doctrine),
        "*FC*",
        escape_md_v2(fc),
    ]

    if comms != "False":
        tg_lines += ["*Comms*", escape_md_v2(comms)]
        if room.strip():
            tg_lines += ["*Комната*", escape_md_v2(room)]

    if notes.strip():
        tg_lines += ["*Примечание*", escape_md_v2(notes)]

    tg_text = "\n".join(tg_lines)

    await post_ping(
        interaction,
        category="STRATOP",
        embed=embed,
        tg_text=tg_text,
    )


@tree.command(name="stratop_preping", description="STRAT-OP pre-ping — с датой/временем")
@app_commands.describe(
    date_et="Дата ET: DD.MM.YYYY или DD.MM",
    time_et="Время ET: HH:MM",
    formup="Место сбора",
    doctrine="Доктрина",
    fc="FC",
    notes="Примечание (опционально)",
    comms="Коммуникации",
    room="Название комнаты (если Comms выбрано)",
)
@app_commands.choices(comms=COMMS_CHOICES)
async def stratop_preping(
    interaction: discord.Interaction,
    date_et: str = "",
    time_et: str = "",
    formup: str = "TBD",
    doctrine: str = "TBD",
    fc: str = "TBD",
    notes: str = "",
    comms: str = "False",
    room: str = "",
):
    await interaction.response.defer()

    if not date_et.strip() or not time_et.strip():
        await interaction.followup.send("Нужно заполнить date_et и time_et (ET).", ephemeral=True)
        return

    try:
        dt = parse_et_date_time(date_et, time_et)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return

    ts = int(dt.timestamp())

    label = "STRAT-OP pre-ping"
    embed = discord.Embed(title=f"🚨 {label}", color=discord.Color.red())
    embed.add_field(
        name="Дата / время",
        value=f"{dt.strftime('%d.%m.%Y %H:%M')} ET\n{discord_time(ts)}",
        inline=False,
    )
    embed.add_field(name="Место сбора", value=formup, inline=False)
    embed.add_field(name="Доктрина", value=doctrine, inline=False)
    embed.add_field(name="FC", value=fc, inline=True)

    if comms != "False":
        embed.add_field(name="Comms", value=comms, inline=True)
        if room.strip():
            embed.add_field(name="Комната", value=room, inline=True)

    if notes.strip():
        embed.add_field(name="Примечание", value=notes, inline=False)

    tg_lines = [
        f"*🚨 {escape_md_v2(label)}*",
        "",
        "*Дата / время*",
        f"{escape_md_v2(dt.strftime('%d.%m.%Y %H:%M'))} ET",
        "*Место сбора*",
        escape_md_v2(formup),
        "*Доктрина*",
        escape_md_v2(doctrine),
        "*FC*",
        escape_md_v2(fc),
    ]

    if comms != "False":
        tg_lines += ["*Comms*", escape_md_v2(comms)]
        if room.strip():
            tg_lines += ["*Комната*", escape_md_v2(room)]

    if notes.strip():
        tg_lines += ["*Примечание*", escape_md_v2(notes)]

    tg_text = "\n".join(tg_lines)

    await post_ping(
        interaction,
        category="STRATOP",
        embed=embed,
        tg_text=tg_text,
    )


@tree.command(name="preping", description="PRE-PING — с датой/временем")
@app_commands.describe(
    date_et="Дата ET: DD.MM.YYYY или DD.MM",
    time_et="Время ET: HH:MM",
    formup="Место сбора",
    doctrine="Доктрина",
    fc="FC",
    notes="Примечание (опционально)",
    comms="Коммуникации",
    room="Название комнаты (если Comms выбрано)",
)
@app_commands.choices(comms=COMMS_CHOICES)
async def preping(
    interaction: discord.Interaction,
    date_et: str = "",
    time_et: str = "",
    formup: str = "TBD",
    doctrine: str = "TBD",
    fc: str = "TBD",
    notes: str = "",
    comms: str = "False",
    room: str = "",
):
    await interaction.response.defer()

    if not date_et.strip() or not time_et.strip():
        await interaction.followup.send("Нужно заполнить date_et и time_et (ET).", ephemeral=True)
        return

    try:
        dt = parse_et_date_time(date_et, time_et)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return

    ts = int(dt.timestamp())

    label = "PRE-PING"
    embed = discord.Embed(title=f"🚨 {label}", color=discord.Color.red())
    embed.add_field(
        name="Дата / время",
        value=f"{dt.strftime('%d.%m.%Y %H:%M')} ET\n{discord_time(ts)}",
        inline=False,
    )
    embed.add_field(name="Место сбора", value=formup, inline=False)
    embed.add_field(name="Доктрина", value=doctrine, inline=False)
    embed.add_field(name="FC", value=fc, inline=True)

    if comms != "False":
        embed.add_field(name="Comms", value=comms, inline=True)
        if room.strip():
            embed.add_field(name="Комната", value=room, inline=True)

    if notes.strip():
        embed.add_field(name="Примечание", value=notes, inline=False)

    tg_lines = [
        f"*🚨 {escape_md_v2(label)}*",
        "",
        "*Дата / время*",
        f"{escape_md_v2(dt.strftime('%d.%m.%Y %H:%M'))} ET",
        "*Место сбора*",
        escape_md_v2(formup),
        "*Доктрина*",
        escape_md_v2(doctrine),
        "*FC*",
        escape_md_v2(fc),
    ]

    if comms != "False":
        tg_lines += ["*Comms*", escape_md_v2(comms)]
        if room.strip():
            tg_lines += ["*Комната*", escape_md_v2(room)]

    if notes.strip():
        tg_lines += ["*Примечание*", escape_md_v2(notes)]

    tg_text = "\n".join(tg_lines)

    await post_ping(
        interaction,
        category="PREPING",
        embed=embed,
        tg_text=tg_text,
    )


@tree.command(name="news", description="Breaking Scuko News — текстовый блок")
@app_commands.describe(
    text="Текст новости",
)
async def breaking_news(
    interaction: discord.Interaction,
    text: str,
):
    await interaction.response.defer()

    if not text.strip():
        await interaction.followup.send("Нужно заполнить текст.", ephemeral=True)
        return

    label = "Breaking Scuko News"
    embed = discord.Embed(title=f"📰 {label}", description=text.strip(), color=discord.Color.blue())

    tg_text = "\n".join([f"*{escape_md_v2(label)}*", "", escape_md_v2(text.strip())])

    await post_ping(
        interaction,
        category="BREAKING_NEWS",
        embed=embed,
        tg_text=tg_text,
    )


@tree.command(name="corp", description="Корпоративная активность — текстовый блок")
@app_commands.describe(
    text="Текст",
)
async def corp_activity(
    interaction: discord.Interaction,
    text: str,
):
    await interaction.response.defer()

    if not text.strip():
        await interaction.followup.send("Нужно заполнить текст.", ephemeral=True)
        return

    label = "Корпоративная активность"
    embed = discord.Embed(title=f"📣 {label}", description=text.strip(), color=discord.Color.green())

    tg_text = "\n".join([f"*{escape_md_v2(label)}*", "", escape_md_v2(text.strip())])

    await post_ping(
        interaction,
        category="CORP_ACTIVITY",
        embed=embed,
        tg_text=tg_text,
    )


@tree.command(name="reping", description="Повторить последний пинг в этом канале (всегда @everyone)")
@app_commands.describe(
)
async def reping(
    interaction: discord.Interaction,
):
    await interaction.response.defer(ephemeral=True)

    if interaction.channel is None:
        return

    data = load_last_ping().get(str(interaction.channel.id))
    if not data:
        await interaction.followup.send("В этом канале нет сохранённого пинга.", ephemeral=True)
        return

    ch = client.get_channel(interaction.channel.id)
    if ch is None:
        await interaction.followup.send("Канал не найден.", ephemeral=True)
        return

    try:
        old_msg = await ch.fetch_message(data["discord_message_id"])
    except Exception:
        await interaction.followup.send("Исходный пинг не найден.", ephemeral=True)
        return

    if not old_msg.embeds:
        await interaction.followup.send("У последнего пинга нет embed.", ephemeral=True)
        return

    # Clone embed but drop old link field; we'll inject a fresh one pointing to the new message.
    embed = embed_without_link_field(old_msg.embeds[0])

    new_msg = await interaction.channel.send(
        content="@everyone",
        embed=embed,
        allowed_mentions=AllowedMentions(everyone=True),
    )
    jump_url = new_msg.jump_url
    embed.add_field(name="Ссылка", value=f"[Ссылка на этот пинг]({jump_url})", inline=False)
    await new_msg.edit(embed=embed)

    # Telegram: resend to the same chat/topic as the original (if we have it).
    tg_chat_id = data.get("tg_chat_id")
    tg_topic_id = data.get("tg_topic_id")
    tg_message_id = None
    tg_text = None
    if tg_chat_id and data.get("tg_text"):
        tg_text = tg_replace_link(str(data["tg_text"]), jump_url)
        tg_message_id = await send_to_telegram(str(tg_chat_id), tg_topic_id, tg_text)

    save_last_ping(
        new_msg.channel.id,
        new_msg.id,
        str(tg_chat_id) if tg_chat_id else None,
        tg_topic_id if tg_chat_id else None,
        tg_message_id,
        tg_text,
    )

    await interaction.followup.send("Reping отправлен.", ephemeral=True)


@tree.command(name="ping_status", description="Статус последнего пинга в этом канале")
@app_commands.describe(status="Новый статус")
async def ping_status(interaction: discord.Interaction, status: str):
    await interaction.response.defer(ephemeral=True)

    if interaction.channel is None:
        return

    data = load_last_ping().get(str(interaction.channel.id))
    if not data:
        await interaction.followup.send("В этом канале нет сохранённого пинга.", ephemeral=True)
        return

    ch = client.get_channel(interaction.channel.id)
    if ch is None:
        await interaction.followup.send("Канал не найден.", ephemeral=True)
        return

    try:
        msg = await ch.fetch_message(data["discord_message_id"])
    except Exception:
        await interaction.followup.send("Сообщение не найдено.", ephemeral=True)
        return

    if not msg.embeds:
        await interaction.followup.send("У последнего пинга нет embed.", ephemeral=True)
        return

    embed = msg.embeds[0]

    # Replace existing "Статус" field if present; otherwise add it.
    status_idx = None
    for i, f in enumerate(embed.fields):
        if f.name == "Статус":
            status_idx = i
            break

    if status_idx is None:
        embed.add_field(name="Статус", value=status, inline=False)
    else:
        embed.set_field_at(status_idx, name="Статус", value=status, inline=False)

    await msg.edit(embed=embed)

    # Telegram: keep original message, append/update status line.
    if data.get("tg_chat_id") and data.get("tg_message_id") and data.get("tg_text"):
        new_text = tg_apply_status(str(data["tg_text"]), status)

        url = f"https://api.telegram.org/bot{TG_TOKEN}/editMessageText"
        payload = {
            "chat_id": data["tg_chat_id"],
            "message_id": data["tg_message_id"],
            "text": new_text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        if data.get("tg_topic_id") is not None:
            payload["message_thread_id"] = data["tg_topic_id"]

        async with aiohttp.ClientSession() as session:
            await session.post(url, json=payload)

        # Persist updated text so next status replacement works.
        save_last_ping(
            interaction.channel.id,
            data["discord_message_id"],
            data.get("tg_chat_id"),
            data.get("tg_topic_id"),
            data.get("tg_message_id"),
            new_text,
        )

    await interaction.followup.send("Готово.", ephemeral=True)


client.run(DISCORD_TOKEN)
