import discord
from discord import app_commands
from python_aternos import Client
import logging
import datetime
import asyncio
import os
import sys
import threading
import traceback
import time
import json
from typing import Optional

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('AternosBot')

# ── Global uncaught-exception hooks ───────────────────────────────────────────
def _handle_exception(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    log.critical('UNCAUGHT EXCEPTION', exc_info=(exc_type, exc_value, exc_tb))

def _handle_thread_exception(args):
    log.critical(f'UNCAUGHT THREAD EXCEPTION in {args.thread}',
                 exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

sys.excepthook = _handle_exception
threading.excepthook = _handle_thread_exception

# ── Config — reads from environment variables (Pterodactyl panel / .env) ──────
TOKEN         = os.environ.get('DISCORD_TOKEN',  '')
ATERNOS_USER  = os.environ.get('ATERNOS_USER',   '')
ATERNOS_PASS  = os.environ.get('ATERNOS_PASS',   '')

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = 0x57F287
RED    = 0xED4245
BLUE   = 0x5865F2
YELLOW = 0xFEE75C
TEAL   = 0x1ABC9C
PURPLE = 0x9B59B6

# ── Aternos client (module-level, re-created on reconnect) ────────────────────
aternos: Optional[Client] = None
myserv = None
_aternos_lock = threading.Lock()

def _aternos_login(retries: int = 6, base_delay: float = 5.0):
    """Login to Aternos with exponential back-off.  Raises on total failure."""
    global aternos, myserv
    for attempt in range(1, retries + 1):
        try:
            log.info(f'Aternos login attempt {attempt}/{retries} ...')
            client = Client()
            client.login(ATERNOS_USER, password=ATERNOS_PASS)
            servers = client.account.list_servers()
            if not servers:
                raise RuntimeError('No Aternos servers found on this account.')
            serv = servers[0]
            serv.fetch()
            with _aternos_lock:
                aternos = client
                myserv  = serv
            log.info(f'Aternos ready  |  Server: {myserv.subdomain}  |  Software: {myserv.software} {myserv.version}')
            return
        except Exception as e:
            log.error(f'Aternos login failed (attempt {attempt}): {e}')
            if attempt < retries:
                delay = base_delay * (2 ** (attempt - 1))
                log.info(f'Retrying in {delay:.0f}s ...')
                time.sleep(delay)
    raise RuntimeError(f'Could not log into Aternos after {retries} attempts.')

def _aternos_reconnect():
    """Re-login silently; used when a session-expired error is detected."""
    log.warning('Aternos session may have expired — reconnecting ...')
    try:
        _aternos_login(retries=4, base_delay=3.0)
        log.info('Aternos reconnect successful.')
    except Exception as e:
        log.error(f'Aternos reconnect failed: {e}')

def _safe_fetch(max_tries: int = 3) -> bool:
    """Fetch server info with retry + automatic reconnect.  Returns True on success."""
    for attempt in range(1, max_tries + 1):
        try:
            with _aternos_lock:
                myserv.fetch()
            return True
        except Exception as e:
            log.warning(f'fetch() failed (attempt {attempt}/{max_tries}): {e}')
            if attempt < max_tries:
                time.sleep(3)
                _aternos_reconnect()
    return False

def _safe_call(fn_name: str, max_tries: int = 3) -> bool:
    """Call myserv.<fn_name>() with retry + automatic reconnect.  Returns True on success."""
    for attempt in range(1, max_tries + 1):
        try:
            with _aternos_lock:
                getattr(myserv, fn_name)()
            return True
        except Exception as e:
            log.warning(f'{fn_name}() failed (attempt {attempt}/{max_tries}): {e}')
            if attempt < max_tries:
                time.sleep(3)
                _aternos_reconnect()
    return False

# Initial login — crash only if we absolutely cannot reach Aternos
_aternos_login()

# ── Discord client ────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(
            intents=intents,
            # Built-in discord.py reconnect + heartbeat handling
        )
        self.tree = app_commands.CommandTree(self)
        self._monitor_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    async def setup_hook(self):
        await self.tree.sync()
        log.info('Slash commands synced globally.')
        self._monitor_task  = self.loop.create_task(autostart_monitor(), name='autostart_monitor')
        self._watchdog_task = self.loop.create_task(monitor_watchdog(),  name='monitor_watchdog')

bot = Bot()

# ── Autostart state ───────────────────────────────────────────────────────────
autostart_enabled: bool = False
autostart_channel: Optional[discord.TextChannel] = None
autostart_set_by:  Optional[str] = None

# State file lives next to this script so it survives bot restarts
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'autostart_state.json')

def save_autostart_state():
    """Persist autostart settings to disk."""
    try:
        data = {
            'enabled':    autostart_enabled,
            'channel_id': autostart_channel.id if autostart_channel else None,
            'set_by':     autostart_set_by,
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        log.info(f'Autostart state saved: enabled={autostart_enabled}, channel={data["channel_id"]}')
    except Exception as e:
        log.error(f'Failed to save autostart state: {e}')

async def load_autostart_state():
    """Restore autostart settings from disk after bot connects."""
    global autostart_enabled, autostart_channel, autostart_set_by
    if not os.path.exists(STATE_FILE):
        log.info('No saved autostart state found — starting fresh.')
        return
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        autostart_enabled = data.get('enabled', False)
        autostart_set_by  = data.get('set_by')
        channel_id        = data.get('channel_id')
        if channel_id:
            autostart_channel = bot.get_channel(int(channel_id))
            if autostart_channel is None:
                log.warning(f'Saved channel ID {channel_id} not found — autostart notifications disabled until re-set.')
        log.info(f'Autostart state restored: enabled={autostart_enabled}, channel={autostart_channel}')
        if autostart_enabled:
            log.info('Auto-Start is ON — monitor will resume automatically.')
    except Exception as e:
        log.error(f'Failed to load autostart state: {e}')

# ── Helper: timestamp footer ───────────────────────────────────────────────────
def footer(embed: discord.Embed):
    embed.set_footer(text=f'Aternos Bot  •  {datetime.datetime.utcnow().strftime("%d %b %Y %H:%M UTC")}')
    return embed

def server_status_color(status: str) -> int:
    return {
        'online':    GREEN,
        'starting':  YELLOW,
        'stopping':  YELLOW,
        'loading':   YELLOW,
        'preparing': YELLOW,
        'offline':   RED,
        'error':     RED,
    }.get(status.lower(), BLUE)

def status_emoji(status: str) -> str:
    return {
        'online':    '🟢',
        'starting':  '🟡',
        'stopping':  '🟡',
        'loading':   '🟡',
        'preparing': '🟡',
        'offline':   '🔴',
        'error':     '❌',
    }.get(status.lower(), '⚪')

# ═══════════════════════════════════════════════════════════════════════════════
#  AUTOSTART BACKGROUND MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

async def autostart_monitor():
    """Polls the server every 60 s and restarts it if autostart is enabled and server goes offline."""
    await bot.wait_until_ready()
    log.info('Autostart monitor started.')
    while not bot.is_closed():
        try:
            await asyncio.sleep(60)
            if not autostart_enabled:
                continue

            ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
            if not ok:
                log.error('[Autostart] Could not fetch server status — skipping this cycle.')
                continue

            status = myserv.status.lower()
            log.info(f'[Autostart] Server status: {status}')

            if status != 'offline':
                continue

            log.info('[Autostart] Server offline — triggering auto-start...')
            if autostart_channel:
                try:
                    embed_alert = discord.Embed(
                        title='🤖  Auto-Start Triggered',
                        description=(
                            '> The server went offline and **Auto-Start** is enabled.\n'
                            '> Booting it back up now — this takes **2 – 4 minutes**!'
                        ),
                        color=YELLOW,
                    )
                    embed_alert.add_field(name='🌐  Server Address', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
                    embed_alert.add_field(name='⚙️  Set by', value=f'`{autostart_set_by}`', inline=True)
                    footer(embed_alert)
                    await autostart_channel.send(embed=embed_alert)
                except Exception as de:
                    log.warning(f'[Autostart] Could not send alert: {de}')

            started = await asyncio.get_event_loop().run_in_executor(None, lambda: _safe_call('start'))
            if not started:
                log.error('[Autostart] start() failed — will retry next cycle.')
                continue

            # Poll up to 5 min for online
            for _ in range(60):
                await asyncio.sleep(5)
                await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
                log.info(f'[Autostart] Polling: {myserv.status}')
                if myserv.status.lower() == 'online':
                    break

            if myserv.status.lower() == 'online':
                if autostart_channel:
                    try:
                        embed_done = discord.Embed(
                            title='🎉  Server Auto-Started Successfully!',
                            description=(
                                '> The server is back online and ready to join!\n\n'
                                '**📋  How to join**\n'
                                '1. Open **Minecraft**\n'
                                '2. Go to **Multiplayer → Add Server**\n'
                                '3. Copy the address below and click **Join Server**'
                            ),
                            color=GREEN,
                        )
                        embed_done.add_field(name='🌐  Server Address  *(copy & paste this)*', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
                        embed_done.add_field(name='🔌  Port',     value=f'`{myserv.port}`',                      inline=True)
                        embed_done.add_field(name='📦  Software', value=f'`{myserv.software} {myserv.version}`', inline=True)
                        embed_done.add_field(name='👥  Slots',    value=f'`{myserv.slots}`',                     inline=True)
                        footer(embed_done)
                        await autostart_channel.send(embed=embed_done)
                    except Exception as de:
                        log.warning(f'[Autostart] Could not send success embed: {de}')
                log.info('[Autostart] Server is back online.')
            else:
                if autostart_channel:
                    try:
                        embed_fail = discord.Embed(
                            title='⚠️  Auto-Start Timed Out',
                            description='The server took too long to start. Will retry next check.\nYou can also use `/start` manually.',
                            color=YELLOW,
                        )
                        footer(embed_fail)
                        await autostart_channel.send(embed=embed_fail)
                    except Exception as de:
                        log.warning(f'[Autostart] Could not send timeout embed: {de}')
                log.warning('[Autostart] Server did not come online within timeout.')

        except asyncio.CancelledError:
            log.info('Autostart monitor cancelled.')
            return
        except Exception as e:
            log.error(f'[Autostart] Unexpected monitor error: {e}\n{traceback.format_exc()}')
            await asyncio.sleep(15)

async def monitor_watchdog():
    """Watchdog: if the autostart monitor task dies for any reason, revive it."""
    await bot.wait_until_ready()
    await asyncio.sleep(30)
    while not bot.is_closed():
        await asyncio.sleep(30)
        try:
            task = bot._monitor_task
            if task is None or task.done():
                exc = task.exception() if (task and not task.cancelled()) else None
                if exc:
                    log.error(f'[Watchdog] Monitor died with: {exc}')
                else:
                    log.warning('[Watchdog] Monitor task ended — restarting it.')
                bot._monitor_task = asyncio.get_event_loop().create_task(
                    autostart_monitor(), name='autostart_monitor'
                )
                log.info('[Watchdog] Monitor task restarted.')
        except Exception as e:
            log.error(f'[Watchdog] Error: {e}')

# ═══════════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

# ── /help ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name='help', description='Show all available bot commands')
async def help_command(interaction: discord.Interaction):
    log.info(f'/help used by {interaction.user} in #{interaction.channel}')
    embed = discord.Embed(
        title='🤖  Aternos Bot — Command Guide',
        description='Control your Minecraft server directly from Discord.\nAll commands are slash commands — just type `/` to get started.',
        color=PURPLE,
    )
    embed.add_field(name='📋  /help',      value='Shows this command guide.',                                              inline=False)
    embed.add_field(name='👋  /hello',     value='The bot greets you personally.',                                         inline=False)
    embed.add_field(name='📊  /status',    value='Shows live server status, player count, address and more.',              inline=False)
    embed.add_field(name='ℹ️  /info',      value='Shows detailed server info: software, version, RAM, slots, MOTD.',       inline=False)
    embed.add_field(name='▶️  /start',     value='Starts the server and pings you when it\'s live.',                       inline=False)
    embed.add_field(name='⏹️  /stop',      value='Stops the server gracefully.',                                           inline=False)
    embed.add_field(name='🔄  /restart',   value='Restarts the server.',                                                   inline=False)
    embed.add_field(
        name='🤖  /autostart',
        value=(
            'Automatically restarts the server whenever it goes offline.\n'
            '`/autostart enabled:True` — turn on  •  `/autostart enabled:False` — turn off'
        ),
        inline=False,
    )
    embed.set_thumbnail(url='https://aternos.org/favicon.ico')
    footer(embed)
    await interaction.response.send_message(embed=embed)

# ── /hello ────────────────────────────────────────────────────────────────────
@bot.tree.command(name='hello', description='Say hello to the bot')
async def hello(interaction: discord.Interaction):
    log.info(f'/hello used by {interaction.user} in #{interaction.channel}')
    embed = discord.Embed(
        title=f'👋  Hey, {interaction.user.display_name}!',
        description='I\'m your Aternos server manager. Use `/help` to see everything I can do.',
        color=TEAL,
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    footer(embed)
    await interaction.response.send_message(embed=embed)

# ── /status ───────────────────────────────────────────────────────────────────
@bot.tree.command(name='status', description='Check live Minecraft server status')
async def status(interaction: discord.Interaction):
    log.info(f'/status used by {interaction.user} in #{interaction.channel}')
    await interaction.response.defer()
    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
        if not ok:
            raise RuntimeError('Could not reach Aternos after multiple retries.')
        s     = myserv.status.lower()
        embed = discord.Embed(title=f'{status_emoji(s)}  Server Status', color=server_status_color(s))
        embed.add_field(name='Status',   value=f'`{myserv.status}`',       inline=True)
        embed.add_field(name='Address',  value=f'`{myserv.address}`',       inline=True)
        embed.add_field(name='Players',  value=f'`{myserv.players_count}`', inline=True)
        if myserv.players_list:
            embed.add_field(name='Online Players', value=', '.join(f'`{p}`' for p in myserv.players_list) or 'None', inline=False)
        footer(embed)
        log.info(f'Status: {myserv.status}  Players: {myserv.players_count}')
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log.error(f'/status error: {e}')
        await interaction.followup.send(embed=_err_embed('Error fetching status', e))

# ── /info ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name='info', description='Show detailed server information')
async def info(interaction: discord.Interaction):
    log.info(f'/info used by {interaction.user} in #{interaction.channel}')
    await interaction.response.defer()
    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
        if not ok:
            raise RuntimeError('Could not reach Aternos after multiple retries.')
        embed = discord.Embed(title='ℹ️  Server Information', color=BLUE)
        embed.add_field(name='🌐  Address',  value=f'`{myserv.address}`',                               inline=True)
        embed.add_field(name='🔌  Port',     value=f'`{myserv.port}`',                                  inline=True)
        embed.add_field(name='📦  Software', value=f'`{myserv.software}`',                              inline=True)
        embed.add_field(name='🏷️  Version', value=f'`{myserv.version}`',                               inline=True)
        embed.add_field(name='💾  RAM',      value=f'`{myserv.ram} MB`',                                inline=True)
        embed.add_field(name='👥  Slots',    value=f'`{myserv.slots}`',                                 inline=True)
        embed.add_field(name='📋  Edition',  value=f'`{"Bedrock" if myserv.is_bedrock else "Java"}`',   inline=True)
        embed.add_field(name='💬  MOTD',     value=f'`{myserv.motd}`',                                  inline=False)
        footer(embed)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log.error(f'/info error: {e}')
        await interaction.followup.send(embed=_err_embed('Error fetching info', e))

# ── /start ────────────────────────────────────────────────────────────────────
@bot.tree.command(name='start', description='Start the Minecraft server')
async def start(interaction: discord.Interaction):
    log.info(f'/start used by {interaction.user} in #{interaction.channel}')
    await interaction.response.defer()
    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
        if not ok:
            raise RuntimeError('Could not reach Aternos after multiple retries.')
        current = myserv.status.lower()

        if current == 'online':
            embed = discord.Embed(
                title='🟢  Server Already Online',
                description=(
                    '> The server is already up and running!\n\n'
                    '**📋  How to join**\n'
                    '1. Open Minecraft\n'
                    '2. Go to **Multiplayer → Add Server**\n'
                    '3. Paste the address below and hit **Join**'
                ),
                color=GREEN,
            )
            embed.add_field(name='🌐  Server Address', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
            embed.add_field(name='🔌  Port',       value=f'`{myserv.port}`',                      inline=True)
            embed.add_field(name='📦  Software',   value=f'`{myserv.software} {myserv.version}`', inline=True)
            embed.add_field(name='👥  Open Slots', value=f'`{myserv.slots}`',                     inline=True)
            footer(embed)
            await interaction.followup.send(embed=embed)
            return

        if current in ('starting', 'loading', 'preparing'):
            embed = discord.Embed(
                title='🟡  Server is Already Starting',
                description='The server is booting up — hang tight!\nUse `/status` to monitor progress.',
                color=YELLOW,
            )
            embed.add_field(name='🌐  Address', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
            footer(embed)
            await interaction.followup.send(embed=embed)
            return

        embed_starting = discord.Embed(
            title='⏳  Booting Server...',
            description=(
                '> Startup usually takes **2 – 4 minutes**.\n'
                '> You\'ll be pinged right here the moment it\'s live!\n\n'
                f'🚀  Started by **{interaction.user.display_name}**'
            ),
            color=YELLOW,
        )
        embed_starting.add_field(name='🌐  Address (ready soon)', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
        embed_starting.add_field(name='📦  Software', value=f'`{myserv.software} {myserv.version}`', inline=True)
        embed_starting.add_field(name='👥  Slots',    value=f'`{myserv.slots}`',                     inline=True)
        embed_starting.add_field(name='🖥️  Edition',  value=f'`{"Bedrock" if myserv.is_bedrock else "Java"}`', inline=True)
        footer(embed_starting)
        await interaction.followup.send(embed=embed_starting)

        log.info('Sending start command to Aternos...')
        started = await asyncio.get_event_loop().run_in_executor(None, lambda: _safe_call('start'))
        if not started:
            raise RuntimeError('Failed to send start command to Aternos.')

        for _ in range(60):
            await asyncio.sleep(5)
            await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
            log.info(f'Polling server status: {myserv.status}')
            if myserv.status.lower() == 'online':
                break

        if myserv.status.lower() == 'online':
            embed_done = discord.Embed(
                title='🎉  Server is LIVE!',
                description=(
                    f'> {interaction.user.mention} your server is ready!\n\n'
                    '**📋  How to join**\n'
                    '1. Open **Minecraft**\n'
                    '2. Go to **Multiplayer → Add Server**\n'
                    '3. Copy the address below and click **Join Server**'
                ),
                color=GREEN,
            )
            embed_done.add_field(name='🌐  Server Address  *(copy & paste this)*', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
            embed_done.add_field(name='🔌  Port',       value=f'`{myserv.port}`',                          inline=True)
            embed_done.add_field(name='📦  Software',   value=f'`{myserv.software} {myserv.version}`',     inline=True)
            embed_done.add_field(name='👥  Open Slots', value=f'`{myserv.slots}`',                         inline=True)
            embed_done.add_field(name='🖥️  Edition',    value=f'`{"Bedrock" if myserv.is_bedrock else "Java"}`', inline=True)
            embed_done.add_field(name='💬  MOTD',       value=f'`{myserv.motd}`',                          inline=False)
            footer(embed_done)
            await interaction.channel.send(content=f'🟢 {interaction.user.mention}', embed=embed_done)
            log.info(f'Server online at {myserv.subdomain}.aternos.me:{myserv.port}')
        else:
            embed_timeout = discord.Embed(
                title='⏳  Taking Longer Than Expected',
                description='The server is still starting up.\nUse `/status` to check progress, or try `/start` again in a few minutes.',
                color=YELLOW,
            )
            embed_timeout.add_field(name='🌐  Address', value=f'```{myserv.subdomain}.aternos.me```', inline=False)
            footer(embed_timeout)
            await interaction.channel.send(embed=embed_timeout)
            log.warning('Server did not come online within the 5-minute timeout.')

    except Exception as e:
        log.error(f'/start error: {e}')
        try:
            await interaction.followup.send(embed=_err_embed('Error Starting Server', e))
        except Exception:
            pass

# ── /stop ─────────────────────────────────────────────────────────────────────
@bot.tree.command(name='stop', description='Stop the Minecraft server')
async def stop(interaction: discord.Interaction):
    log.info(f'/stop used by {interaction.user} in #{interaction.channel}')
    await interaction.response.defer()
    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
        if not ok:
            raise RuntimeError('Could not reach Aternos after multiple retries.')
        if myserv.status.lower() == 'offline':
            embed = discord.Embed(title='🔴  Server Already Offline', description='The server is not running. Use `/start` to boot it up.', color=RED)
            footer(embed)
            await interaction.followup.send(embed=embed)
            return

        stopped = await asyncio.get_event_loop().run_in_executor(None, lambda: _safe_call('stop'))
        if not stopped:
            raise RuntimeError('Failed to send stop command to Aternos.')
        log.info(f'Stop command sent by {interaction.user}')
        embed = discord.Embed(
            title='⏹️  Shutting Down...',
            description=(
                '> The server is gracefully shutting down.\n\n'
                f'🛑  Stopped by **{interaction.user.display_name}**\n'
                'Use `/status` to confirm it\'s fully offline.'
            ),
            color=RED,
        )
        embed.add_field(name='🌐  Server',   value=f'```{myserv.subdomain}.aternos.me```',           inline=False)
        embed.add_field(name='📦  Software', value=f'`{myserv.software} {myserv.version}`', inline=True)
        footer(embed)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log.error(f'/stop error: {e}')
        try:
            await interaction.followup.send(embed=_err_embed('Error Stopping Server', e))
        except Exception:
            pass

# ── /restart ──────────────────────────────────────────────────────────────────
@bot.tree.command(name='restart', description='Restart the Minecraft server')
async def restart(interaction: discord.Interaction):
    log.info(f'/restart used by {interaction.user} in #{interaction.channel}')
    await interaction.response.defer()
    try:
        ok = await asyncio.get_event_loop().run_in_executor(None, _safe_fetch)
        if not ok:
            raise RuntimeError('Could not reach Aternos after multiple retries.')
        restarted = await asyncio.get_event_loop().run_in_executor(None, lambda: _safe_call('restart'))
        if not restarted:
            raise RuntimeError('Failed to send restart command to Aternos.')
        log.info(f'Restart command sent by {interaction.user}')
        embed = discord.Embed(
            title='🔄  Restarting Server...',
            description=(
                '> The server is rebooting — this takes **2 – 4 minutes**.\n\n'
                f'🔁  Restarted by **{interaction.user.display_name}**\n'
                'Use `/status` to track the progress.'
            ),
            color=BLUE,
        )
        embed.add_field(name='🌐  Server Address', value=f'```{myserv.subdomain}.aternos.me```',       inline=False)
        embed.add_field(name='🔌  Port',       value=f'`{myserv.port}`',                      inline=True)
        embed.add_field(name='📦  Software',   value=f'`{myserv.software} {myserv.version}`', inline=True)
        embed.add_field(name='👥  Slots',      value=f'`{myserv.slots}`',                     inline=True)
        footer(embed)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        log.error(f'/restart error: {e}')
        try:
            await interaction.followup.send(embed=_err_embed('Error Restarting Server', e))
        except Exception:
            pass

# ── /autostart ────────────────────────────────────────────────────────────────
@bot.tree.command(name='autostart', description='Auto-restart the server whenever it goes offline')
@app_commands.describe(enabled='True to enable auto-start, False to disable it')
async def autostart(interaction: discord.Interaction, enabled: bool):
    global autostart_enabled, autostart_channel, autostart_set_by
    autostart_enabled = enabled
    autostart_channel = interaction.channel
    autostart_set_by  = str(interaction.user)
    save_autostart_state()
    log.info(f'/autostart set to {enabled} by {interaction.user} — notifications → #{interaction.channel}')
    if enabled:
        embed = discord.Embed(
            title='🤖  Auto-Start  ·  ENABLED',
            description=(
                '> The bot will now **automatically restart** the server\n'
                '> whenever it detects it has gone offline.\n\n'
                f'📡  Notifications will appear right here in **#{interaction.channel.name}**\n'
                '⏱️  Server is checked every **60 seconds**'
            ),
            color=GREEN,
        )
        embed.add_field(name='🌐  Server',      value=f'```{myserv.subdomain}.aternos.me```',         inline=False)
        embed.add_field(name='⚙️  Enabled by',  value=f'`{interaction.user.display_name}`',            inline=True)
        embed.add_field(name='📢  Channel',      value=f'<#{interaction.channel.id}>',                 inline=True)
        embed.add_field(name='ℹ️  How to disable', value='Run `/autostart enabled:False` at any time.', inline=False)
    else:
        embed = discord.Embed(
            title='🤖  Auto-Start  ·  DISABLED',
            description=(
                '> Auto-Start has been turned off.\n'
                '> The server will **not** restart automatically if it goes offline.\n\n'
                'Use `/start` to bring it back up manually, or\n'
                'run `/autostart enabled:True` to turn it on again.'
            ),
            color=RED,
        )
        embed.add_field(name='⚙️  Disabled by', value=f'`{interaction.user.display_name}`', inline=True)
    footer(embed)
    await interaction.response.send_message(embed=embed)

# ═══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    log.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    log.info(f'Connected to {len(bot.guilds)} guild(s): {[g.name for g in bot.guilds]}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name='your Minecraft server 🎮'))
    await load_autostart_state()

@bot.event
async def on_disconnect():
    log.warning('Discord connection lost — discord.py will auto-reconnect.')

@bot.event
async def on_resumed():
    log.info('Discord session resumed successfully.')

@bot.event
async def on_guild_join(guild: discord.Guild):
    log.info(f'Joined new guild: {guild.name} (ID: {guild.id})')

@bot.event
async def on_error(event, *args, **kwargs):
    log.error(f'Unhandled error in event "{event}":\n{traceback.format_exc()}')

@bot.event
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    log.error(f'App command error: {error}')
    embed = discord.Embed(title='❌  Something went wrong', description=str(error), color=RED)
    footer(embed)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        log.error(f'Could not send error response: {e}')

# ── Shared error embed helper ──────────────────────────────────────────────────
def _err_embed(title: str, err: Exception) -> discord.Embed:
    embed = discord.Embed(title=f'❌  {title}', description=f'```{err}```', color=RED)
    footer(embed)
    return embed

# ═══════════════════════════════════════════════════════════════════════════════
#  RUN — with infinite reconnect loop
# ═══════════════════════════════════════════════════════════════════════════════

log.info('Starting Discord bot...')
_restart_delay = 5
while True:
    try:
        bot.run(TOKEN, log_handler=None, reconnect=True)
        # bot.run() only returns if the event loop ends cleanly
        log.warning('bot.run() returned — restarting in 5 s ...')
    except discord.LoginFailure:
        log.critical('Invalid Discord token — cannot reconnect. Check DISCORD_TOKEN.')
        sys.exit(1)
    except KeyboardInterrupt:
        log.info('Keyboard interrupt — shutting down.')
        sys.exit(0)
    except Exception as e:
        log.error(f'bot.run() crashed: {e}\n{traceback.format_exc()}')
        log.info(f'Restarting bot in {_restart_delay}s ...')
    time.sleep(_restart_delay)
    _restart_delay = min(_restart_delay * 2, 60)
