# Remote Access Guide for PictureViewer Server

PictureViewer is designed for **local network access only**. Your iPhone and computer must be on the same Wi-Fi network. To access your photos from outside your home (e.g. from work, traveling, or on mobile data), you need a **secure tunnel** — never expose the server directly to the internet.

Below are 4 recommended methods, from easiest to most technical.

---

## Option 1: Tailscale (Easiest — Recommended)

**What it is:** A free mesh VPN that creates a private encrypted network between your devices. No port forwarding, no firewall changes, works through NAT.

**Cost:** Free for personal use (up to 100 devices)

### Setup

#### On your computer (where PictureViewer Server runs):

1. **Download Tailscale:**
   - Windows: https://tailscale.com/download/windows
   - macOS: https://tailscale.com/download/macos
   - Linux: https://tailscale.com/download/linux

2. **Install and sign in** with Google, Microsoft, or GitHub account

3. **Note your Tailscale IP** — it will look like `100.x.y.z`
   - Windows: Check the Tailscale icon in system tray → "My IP"
   - macOS: Check the Tailscale menu bar icon
   - Linux: Run `tailscale ip -4`

4. **Start PictureViewer Server** as normal

#### On your iPhone:

1. **Download Tailscale** from the App Store:
   https://apps.apple.com/app/tailscale/id1470499037

2. **Sign in** with the same account you used on your computer

3. **Connect** — toggle Tailscale on

4. **In PictureViewer app**, enter your Tailscale IP as the server address:
   ```
   http://100.x.y.z:8500
   ```

5. Enter your access code and connect — done!

### Why Tailscale?
- Zero configuration on your router
- Works through any firewall or NAT
- End-to-end encrypted (WireGuard under the hood)
- Free for personal use
- Works on all platforms

---

## Option 2: WireGuard VPN (Most Secure)

**What it is:** A modern, fast, lightweight VPN protocol. You run a WireGuard server on your home network and connect from anywhere.

**Cost:** Free (open source)

### Setup

#### On your computer or router:

1. **Install WireGuard:**
   - Windows: https://www.wireguard.com/install/
   - macOS: `brew install wireguard-tools` or App Store
   - Linux: `sudo apt install wireguard` (Ubuntu/Debian)

2. **Generate keys:**
   ```bash
   wg genkey | tee server_private.key | wg pubkey > server_public.key
   wg genkey | tee phone_private.key | wg pubkey > phone_public.key
   ```

3. **Create server config** `/etc/wireguard/wg0.conf`:
   ```ini
   [Interface]
   PrivateKey = <contents of server_private.key>
   Address = 10.0.0.1/24
   ListenPort = 51820

   [Peer]
   PublicKey = <contents of phone_public.key>
   AllowedIPs = 10.0.0.2/32
   ```

4. **Start WireGuard:**
   ```bash
   # Linux
   sudo wg-quick up wg0
   sudo systemctl enable wg-quick@wg0  # auto-start on boot

   # Windows/macOS — use the WireGuard app to import the config
   ```

5. **Port forward** UDP port 51820 on your router to your computer
   - Log into your router (usually http://192.168.1.1)
   - Find Port Forwarding settings
   - Forward external UDP 51820 → your computer's local IP, port 51820

6. **Find your public IP:** Visit https://whatismyip.com

#### On your iPhone:

1. **Download WireGuard** from App Store:
   https://apps.apple.com/app/wireguard/id1441195209

2. **Create a new tunnel** with this config:
   ```ini
   [Interface]
   PrivateKey = <contents of phone_private.key>
   Address = 10.0.0.2/24
   DNS = 1.1.1.1

   [Peer]
   PublicKey = <contents of server_public.key>
   Endpoint = <your-public-ip>:51820
   AllowedIPs = 10.0.0.0/24, <your-local-subnet>/24
   PersistentKeepalive = 25
   ```

3. **Connect** the VPN tunnel

4. **In PictureViewer app**, use your computer's local IP:
   ```
   http://192.168.1.x:8500
   ```
   Or the WireGuard tunnel IP:
   ```
   http://10.0.0.1:8500
   ```

### Tips
- Use a Dynamic DNS service (like DuckDNS — free) if your public IP changes
- Keep your private keys secret — never share them
- WireGuard is extremely fast — minimal battery impact

---

## Option 3: Cloudflare Tunnel (No Port Forwarding)

**What it is:** A free service from Cloudflare that creates an encrypted tunnel from your computer to Cloudflare's network, giving you an HTTPS URL accessible from anywhere. No ports to open on your router.

**Cost:** Free

**Requires:** A free Cloudflare account and a domain name (can use a free one)

### Setup

#### On your computer:

1. **Sign up** for Cloudflare: https://dash.cloudflare.com/sign-up

2. **Install cloudflared:**
   - Windows: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
   - macOS: `brew install cloudflared`
   - Linux:
     ```bash
     # Debian/Ubuntu
     curl -fsSL https://pkg.cloudflare.com/cloudflared-linux-amd64.deb -o cloudflared.deb
     sudo dpkg -i cloudflared.deb
     ```

3. **Authenticate:**
   ```bash
   cloudflared tunnel login
   ```
   This opens a browser — select your domain.

4. **Create tunnel:**
   ```bash
   cloudflared tunnel create pictureviewer
   ```

5. **Configure the tunnel.** Create `~/.cloudflared/config.yml`:
   ```yaml
   tunnel: pictureviewer
   credentials-file: /path/to/.cloudflared/<tunnel-id>.json

   ingress:
     - hostname: photos.yourdomain.com
       service: http://localhost:8500
     - service: http_status:404
   ```

6. **Add DNS record:**
   ```bash
   cloudflared tunnel route dns pictureviewer photos.yourdomain.com
   ```

7. **Start the tunnel:**
   ```bash
   cloudflared tunnel run pictureviewer
   ```

8. **Auto-start (optional):**
   ```bash
   # Linux
   sudo cloudflared service install
   sudo systemctl enable cloudflared

   # macOS
   sudo cloudflared service install
   ```

#### On your iPhone:

1. **In PictureViewer app**, enter your tunnel URL:
   ```
   https://photos.yourdomain.com
   ```

2. Enter your access code — done! Full HTTPS encryption.

### Tips
- Cloudflare adds DDoS protection automatically
- You get HTTPS for free — no SSL certificate needed
- If you don't have a domain, you can get a free one from Freenom or use Cloudflare's `trycloudflare.com` for quick testing:
  ```bash
  cloudflared tunnel --url http://localhost:8500
  ```
  This gives you a temporary public URL instantly.

---

## Option 4: SSH Tunnel (Technical)

**What it is:** Uses SSH to create an encrypted tunnel. Good if you already have SSH access to your computer from the internet.

**Cost:** Free

**Requires:** SSH server running on your computer, port 22 forwarded on your router

### Setup

#### Prerequisites:
- SSH server on your computer:
  - macOS: Enable in System Settings → General → Sharing → Remote Login
  - Linux: `sudo apt install openssh-server`
  - Windows: Enable OpenSSH Server in Settings → Apps → Optional Features

- Port 22 forwarded on your router (or custom SSH port)

#### From your iPhone (using a terminal app):

1. **Download an SSH app** like Termius or Blink Shell from the App Store

2. **Create the tunnel:**
   ```bash
   ssh -L 8500:localhost:8500 username@your-public-ip
   ```

3. **In PictureViewer app**, connect to:
   ```
   http://localhost:8500
   ```

#### Alternative — Persistent tunnel from another device:

If you have a Linux server or always-on machine with access to your home network:
```bash
# On the remote machine
ssh -N -L 0.0.0.0:8500:home-pc-ip:8500 username@your-public-ip
```

### Tips
- Use SSH keys instead of passwords for better security
- Add `-N` flag for tunnel-only (no shell)
- Use `autossh` for automatic reconnection on Linux

---

## Quick Comparison

| Method | Ease of Setup | Port Forwarding | Cost | Speed |
|--------|:---:|:---:|:---:|:---:|
| **Tailscale** | Very Easy | No | Free | Fast |
| **WireGuard** | Medium | Yes (UDP 51820) | Free | Fastest |
| **Cloudflare Tunnel** | Medium | No | Free | Good |
| **SSH Tunnel** | Technical | Yes (TCP 22) | Free | Good |

## Security Reminders

- **Never** expose PictureViewer Server directly to the internet without a VPN/tunnel
- **Never** use port forwarding for the server port (8500) — use a VPN instead
- **Always** use a strong, unique access code
- **Keep** your server software updated
- Consider using a **firewall** to restrict which IPs can access the server
