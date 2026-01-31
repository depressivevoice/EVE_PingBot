import os
import re
import logging
from datetime import datetime, timezone
import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv
from discord import AllowedMentions

load_dotenv()
logging.basicConfig(level=logging.INFO)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TG_TOPIC_ID = os.getenv("TELEGRAM_TOPIC_ID")

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set.")
if not TG_TOKEN or not TG_CHAT_ID or not TG_TOPIC_ID:
    raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / TELEGRAM_TOPIC_ID is not set.")


def escape_md_v2(text: str) -> str:
    """Escape Telegram MarkdownV2 special chars."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


async def send_to_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "message_thread_id": TG_TOPIC_ID,
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as r:
                if r.status >= 400:
                    raise RuntimeError(await r.text())
    except aiohttp.ClientError as e:
        raise RuntimeError(f"Failed to send to Telegram: {e}")


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
        try:
            return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date/time values.")
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
    """Discord timestamp tags: full local + relative countdown."""
    return f"<t:{ts}:F> • ⏳ <t:{ts}:R>"


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

PING_TYPES = [
    app_commands.Choice(name="STRAT-OP Флот СОБИРАЕТСЯ!", value="STRATOP_FORMING"),
    app_commands.Choice(name="STRAT-OP pre-ping", value="STRATOP_PREPING"),
    app_commands.Choice(name="PRE-PING", value="PREPING"),
]

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


@tree.command(name="ping", description="Пинг на ДС и ТГ")
@app_commands.describe(
    ping_type="Тип пинга",
    date_et="Дата ET: DD.MM.YYYY или DD.MM (для pre-ping)",
    time_et="Время ET: HH:MM (для pre-ping)",
    formup="Место сбора",
    doctrine="Доктрина",
    fc="FC",
    notes="Примечание (опционально)",
    include_link="Добавить ссылку на оригинальный пинг",
    comms="Коммуникации",
    room="Название комнаты (если Comms выбрано)",
    tg="Репост в Telegram",
)
@app_commands.choices(ping_type=PING_TYPES, comms=COMMS_CHOICES)
async def eveping(
    interaction: discord.Interaction,
    ping_type: app_commands.Choice[str],
    date_et: str = "",
    time_et: str = "",
    formup: str = "TBD",
    doctrine: str = "TBD",
    fc: str = "TBD",
    notes: str = "",
    include_link: bool = True,
    comms: str = "False",
    room: str = "",
    tg: bool = True,
):
    await interaction.response.defer()

    label = ping_type.name

    dt: datetime | None = None
    ts: int | None = None

    # STRATOP собирается — без даты и времени
    if ping_type.value == "STRATOP_FORMING":
        dt = None
        ts = None
    else:
        # STRATOP pre-ping / PRE-PING — раздельный ввод
        if not date_et.strip() or not time_et.strip():
            await interaction.followup.send(
                "Для этого типа пинга нужно заполнить date_et и time_et (ET).",
                ephemeral=True,
            )
            return
        try:
            dt = parse_et_date_time(date_et, time_et)
        except ValueError as e:
            await interaction.followup.send(str(e), ephemeral=True)
            return
        ts = int(dt.timestamp())

    embed = discord.Embed(title=f"🚨 {label}", color=discord.Color.red())

    if dt is not None and ts is not None:
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

    msg = await interaction.followup.send(
        content="@everyone",
        embed=embed,
        allowed_mentions=AllowedMentions(everyone=True),
    )
    jump_url = msg.jump_url

    if include_link:
        embed.add_field(name="Ссылка", value=f"[Ссылка на этот пинг]({jump_url})", inline=False)
        await msg.edit(content="@everyone", embed=embed, allowed_mentions=AllowedMentions(everyone=True))

    if tg:
        tg_lines = []
        tg_lines.append(f"*🚨 {escape_md_v2(label)}*")
        tg_lines.append("")

        if dt is not None:
            tg_lines.append("*Дата / время*")
            tg_lines.append(f"{escape_md_v2(dt.strftime('%d.%m.%Y %H:%M'))} ET")

        tg_lines.append("*Место сбора*")
        tg_lines.append(escape_md_v2(formup))

        tg_lines.append("*Доктрина*")
        tg_lines.append(escape_md_v2(doctrine))

        tg_lines.append("*FC*")
        tg_lines.append(escape_md_v2(fc))

        if comms != "False":
            tg_lines.append("*Comms*")
            tg_lines.append(escape_md_v2(comms))

            if room.strip():
                tg_lines.append("*Комната*")
                tg_lines.append(escape_md_v2(room))

        if notes.strip():
            tg_lines.append("*Примечание*")
            tg_lines.append(escape_md_v2(notes))
            tg_lines.append("")

        if include_link:
            tg_lines.append(f"[Ссылка на пинг]({escape_md_v2(jump_url)})")
            tg_lines.append("")

        tg_text = "\n".join(tg_lines)
        await send_to_telegram(tg_text)


client.run(DISCORD_TOKEN)
