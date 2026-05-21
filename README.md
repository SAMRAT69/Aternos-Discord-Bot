# 🤖 Aternos Discord Bot

A powerful Discord bot for managing your Aternos Minecraft server directly from Discord.

Control your Minecraft server with slash commands, automatic monitoring, auto-restart, live server status, and rich Discord embeds.

---

## ✨ Features

✅ Start, stop and restart your server  
✅ Live server status monitoring  
✅ Automatic server restart (Auto-Start)  
✅ Persistent Auto-Start settings after reboot  
✅ Rich Discord slash commands  
✅ Automatic reconnect system  
✅ Crash recovery watchdog  
✅ Aternos session auto-reconnect  
✅ Production-ready logging system

---

## 📸 Commands

| Command | Description |
|----------|-------------|
| `/help` | Show command list |
| `/hello` | Friendly greeting |
| `/status` | View live server status |
| `/info` | Detailed server information |
| `/start` | Start the Minecraft server |
| `/stop` | Stop the Minecraft server |
| `/restart` | Restart the Minecraft server |
| `/autostart enabled:True/False` | Auto restart server when offline |

---

## 🛠 Requirements

- Python **3.9+**
- Discord Bot Token
- Aternos Account
- A Minecraft server hosted on Aternos

---

## 📦 Installation

Clone the repository:

```bash
git clone https://github.com/SAMRAT69/Aternos-Discord-Bot.git
cd Aternos-Discord-Bot
```

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## ⚙️ Environment Variables

Set the following variables:

```env
DISCORD_TOKEN=your_discord_bot_token
ATERNOS_USER=your_aternos_username
ATERNOS_PASS=your_aternos_password
```

---

## 🚀 Run the Bot

Start using:

```bash
python main.py
```

The launcher automatically:

- Checks Python version
- Validates environment variables
- Installs dependencies
- Applies compatibility patches
- Starts the bot

---

## 🤖 Auto-Start System

Enable automatic restart:

```text
/autostart enabled:True
```

Disable it:

```text
/autostart enabled:False
```

The bot checks your server every **60 seconds** and automatically starts it if it goes offline.

Auto-start settings are saved and restored after bot restarts.

---

## 🎮 Supported Features

### Server Management
- Start server
- Stop server
- Restart server
- View status

### Server Information
- Player count
- Server address
- Port
- Software version
- RAM
- MOTD
- Edition (Java/Bedrock)

### Reliability
- Automatic reconnect
- Crash recovery
- Session refresh
- Retry system
- Watchdog monitoring

---

## 📝 Notes

- Startup may take **2–4 minutes** depending on Aternos queue.
- Slash commands may take a few minutes to appear globally.
- Environment variables are recommended for security.

---

## ❤️ Credits

**MADE BY — .samratt**  
`1154000002927050853`

Discord: https://discord.com/users/1154000002927050853
GitHub: https://github.com/SAMRAT69

---

## 📜 License

This project is for educational and personal server management purposes.

Use responsibly.
