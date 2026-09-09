# Ubuntu deployment

This deployment uses Nginx, two sandboxed systemd services, UFW, Certbot, and rclone. Run it only after the domain points to the VPS.

## 1. Prepare the server

Create an Ubuntu 24.04 VPS with an SSH key. Before disabling root login, create a normal sudo account from the initial root session:

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
```

Open a second terminal and confirm that `ssh deploy@SERVER_IP` works. Keep the original session open until installation finishes.

## 2. Configure DNS

Create an `A` record pointing the domain to the VPS IPv4 address. Add an `AAAA` record only when IPv6 is configured. Wait until the records resolve before requesting the certificate.

## 3. Install Steam-KaKaBase

As the tested non-root sudo user:

```bash
git clone https://github.com/kangkang1337/Steam-KaKaBase.git
cd Steam-KaKaBase
sudo bash ./deploy/install_ubuntu.sh steam.example.com admin@example.com
```

The script refuses to harden SSH unless the current sudo user has `authorized_keys`. It installs the application at `/opt/steam-kakabase`, creates a random admin token, validates Nginx, starts Web and crawler separately, enables UFW, disables root/password SSH, and obtains a Let's Encrypt certificate.

Add `STEAM_API_KEY` and `ITAD_API_KEY` to `/opt/steam-kakabase/.env` without printing them into shell history. Then restart only the crawler:

```bash
sudoedit /opt/steam-kakabase/.env
sudo systemctl restart steam-kakabase-crawler
```

## 4. Configure offsite backup

Vultr Object Storage exposes an S3-compatible endpoint. Create a restricted bucket credential, then configure rclone interactively:

```bash
sudo mkdir -p /etc/rclone
sudo rclone config --config /etc/rclone/rclone.conf
sudo chown root:steamkb /etc/rclone/rclone.conf
sudo chmod 640 /etc/rclone/rclone.conf
```

Set a remote destination and retention period in `/opt/steam-kakabase/.env`:

```env
STEAMKB_OFFSITE_REMOTE=vultr:your-private-bucket/steam-kakabase
STEAMKB_OFFSITE_RETENTION_DAYS=30
```

Test one upload before enabling the timer:

```bash
sudo systemctl start steam-kakabase-backup.service
sudo systemctl status steam-kakabase-backup.service
sudo systemctl enable --now steam-kakabase-backup.timer
systemctl list-timers steam-kakabase-backup.timer
```

The upload script validates the SQLite backup before transfer. Credentials remain in `/etc/rclone/rclone.conf`, which is not part of the repository.

## 5. Verify and operate

```bash
curl --fail https://steam.example.com/health
sudo systemctl status steam-kakabase-web steam-kakabase-crawler
sudo journalctl -u steam-kakabase-web -u steam-kakabase-crawler --since today
sudo nginx -t
sudo ufw status verbose
sudo sshd -t
```

Updates:

```bash
cd Steam-KaKaBase
git pull --ff-only
sudo bash ./deploy/install_ubuntu.sh steam.example.com admin@example.com
```

Never expose port `8765` through the cloud firewall. Keep only TCP `22`, `80`, and `443` open, and restrict port `22` to your own IP when practical.
