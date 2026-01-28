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
# Заменён print на логирование для лучшей отладки
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
    # Добавил обработку сетевых исключений
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


def parse_et_datetime(s: str) -> datetime:
    """
    Accept ET(=UTC):
      - 'DD.MM.YYYY HH:MM'
      - 'DD.MM HH:MM' (year inferred from current UTC; if already past, rolls to next year)
    """
    s = s.strip()
    try:
        d = datetime.strptime(s, "%d.%m.%Y %H:%M")
        return d.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    m = re.fullmatch(r"(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})", s)
    if not m:
        raise ValueError("Bad datetime format. Use 'DD.MM.YYYY HH:MM ET' or 'DD.MM HH:MM ET'.")

    day, month, hour, minute = map(int, m.groups())
    now = datetime.now(timezone.utc)
    #валидация даты с try-except для предотвращения ValueError от datetime()
    try:
        d = datetime(now.year, month, day, hour, minute, tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("Invalid date values.")
    if d < now:
        try:
            d = datetime(now.year + 1, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid date values.")
    return d


def discord_time(ts: int) -> str:
    """Discord timestamp tags: full local + relative countdown."""
    return f"<t:{ts}:F> • ⏳ <t:{ts}:R>"


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

PING_TYPES = [
    app_commands.Choice(name="STRATOP", value="STRATOP"),
    app_commands.Choice(name="STRATOP pre-ping", value="STRATOP_PREPING"),
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
    when_et="Дата/время ET: DD.MM.YYYY HH:MM или DD.MM HH:MM",
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
    when_et: str,
    formup: str = "TBD",
    doctrine: str = "TBD",
    fc: str = "TBD",
    notes: str = "",
    include_link: bool = True,
    comms: str = "False",
    room: str = "",
    tg: bool = True,
):
    # Ack early to avoid 3s timeout
    await interaction.response.defer()

    try:
        dt = parse_et_datetime(when_et)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return

    ts = int(dt.timestamp())
    label = ping_type.name  # human label

    # Шаблон сообщения
    # Убрал дублирование: поля Доктрина и FC добавляются одинаково для всех типов пингов
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

    # Отправить @everyone
    msg = await interaction.followup.send(content="@everyone", embed=embed, allowed_mentions=AllowedMentions(everyone=True))
    jump_url = msg.jump_url

    if include_link:
        embed.add_field(name="Ссылка", value=f"[Ссылка на этот пинг]({jump_url})", inline=False)
        await msg.edit(content="@everyone", embed=embed, allowed_mentions=AllowedMentions(everyone=True))

    # Telegram repost
    if tg:
        tg_lines = []
        tg_lines.append(f"*🚨 {escape_md_v2(label)}*")
        tg_lines.append("")

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
