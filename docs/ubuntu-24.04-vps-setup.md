# Ubuntu 24.04 VPS Setup Guide

This guide assumes a fresh Ubuntu 24.04 server and a normal non-root user with `sudo`.

## 1. Update the box

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip ca-certificates
```

Optional but recommended:

```bash
sudo apt install -y ufw
sudo ufw allow OpenSSH
sudo ufw enable
```

## 2. Create a dedicated service user

```bash
sudo adduser --system --group --home /opt/rtfm-bot rtfm-bot
sudo mkdir -p /opt/rtfm-bot
sudo chown -R rtfm-bot:rtfm-bot /opt/rtfm-bot
```

## 3. Clone the project

Run the next commands as the service user:

```bash
sudo -u rtfm-bot -H bash
cd /opt/rtfm-bot
git clone https://github.com/YOUR-ACCOUNT/rtfm-read-bot-irp.git app
cd app
```

If the repo already exists somewhere else, copy it into `/opt/rtfm-bot/app` instead.

## 4. Create the virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Create the environment file

Start from the checked-in template:

```bash
cp .env.example .env
nano .env
```

Fill in at least:

- `DISCORD_BOT_TOKEN`
- `POLLINATIONS_API_KEY`
- `ALLOWED_GUILD_ID`
- `ALLOWED_ROLE_ID`
- `BOT_OWNER_USER_ID`

Useful deployment note:

- Set `COMMAND_GUILD_ID` to your Discord server ID while you are iterating so slash command changes sync quickly.
- Once the commands look stable, you can leave it set or clear it if you want global sync behavior.

## 6. Verify the bot can start

```bash
source .venv/bin/activate
python3 -m unittest
python3 main.py
```

Wait for the bot to log in successfully, then stop it with `Ctrl+C`.

Before this works, make sure the Discord application has:

- the bot invited to the correct server
- `MESSAGE CONTENT INTENT` enabled in the Discord developer portal

## 7. Create a systemd service

Exit the service-user shell if you are still inside it, then create the service:

```bash
sudo nano /etc/systemd/system/rtfm-bot.service
```

Use this unit:

```ini
[Unit]
Description=Reader of the Manual Discord bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=rtfm-bot
Group=rtfm-bot
WorkingDirectory=/opt/rtfm-bot/app
ExecStart=/opt/rtfm-bot/app/.venv/bin/python /opt/rtfm-bot/app/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rtfm-bot.service
sudo systemctl status rtfm-bot.service
```

## 8. Useful operations

View logs:

```bash
sudo journalctl -u rtfm-bot.service -f
```

Restart after updating code:

```bash
sudo -u rtfm-bot -H bash
cd /opt/rtfm-bot/app
git pull
source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest
exit
sudo systemctl restart rtfm-bot.service
```

## 9. Things worth checking if it fails

- The VPS needs outbound HTTPS access to Discord, Read the Docs, GitHub raw content, and Pollinations.
- If slash commands seem missing, confirm the bot actually started and synced commands without errors.
- If docs answers are bad or empty, check whether the cache refresh succeeded on startup.
- Runtime data lives in `data/` by default, including the SQLite database and docs cache metadata.
