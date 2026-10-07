# Remote Access Guide for Lumina Gallery Server

Lumina Gallery Server is designed for **local network access only**. Your iPhone and computer must be on the same Wi-Fi network. To access your photos from outside your home (e.g. from work, traveling, or on mobile data), you need a **secure tunnel** — never expose the server directly to the internet.

Below are the options, from easiest to most technical (Option 3 is listed only to explain why it is not supported).

---

## Option 1: Tailscale (Easiest — Recommended)

**What it is:** A free mesh VPN that creates a private encrypted network between your devices. No port forwarding, no firewall changes, works through NAT.

**Cost:** Free for personal use (up to 100 devices)

### Setup

#### On your computer (where Lumina Gallery Server runs):

1. **Download Tailscale:**
   - Windows: https://tailscale.com/download/windows
   - macOS: https://tailscale.com/download/macos
   - Linux: https://tailscale.com/download/linux

2. **Install and sign in** with Google, Microsoft, or GitHub account

3. **Note your Tailscale IP** — it will look like `100.x.y.z`
   - Windows: Check the Tailscale icon in system tray → "My IP"
   - macOS: Check the Tailscale menu bar icon
   - Linux: Run `tailscale ip -4`

4. **Start Lumina Gallery Server** as normal

#### On your iPhone:

1. **Download Tailscale** from the App Store:
   https://apps.apple.com/app/tailscale/id1470499037

2. **Sign in** with the same account you used on your computer

3. **Connect** — toggle Tailscale on

4. **In Lumina Gallery app**, enter your Tailscale IP as the server address:
   ```
   http://100.x.y.z:8500
   ```

5. Enter your access code and connect — done!

### Windows firewall note

The Windows installer's firewall rule only allows your **local subnet**. Tailscale
devices connect from `100.x.y.z` addresses, so also allow Tailscale's range
(Administrator PowerShell, once):

```powershell
New-NetFirewallRule -DisplayName "Lumina Gallery Server (Tailscale)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8500,8543 -RemoteAddress 100.64.0.0/10
```

If you'd rather use your computer's MagicDNS name (e.g. `my-pc.tailnet-name.ts.net`)
instead of the 100.x address, add it to `allowed_hosts` in `config.json`, e.g.
`"allowed_hosts": ["my-pc.tailnet-name.ts.net"]`, and restart the server.

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

4. **In Lumina Gallery app**, use your computer's local IP:
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

## Option 3: Cloudflare Tunnel — not supported

**Don't use a Cloudflare Tunnel (or ngrok, `trycloudflare.com`, or any public
URL) with this server.** It can't work safely with the app today:

- A tunnel puts your photo server on the **public internet**, where anyone can
  try access codes against it.
- The app pins the server's own self-signed certificate and switches to the
  HTTPS port the server reports (8543). Through a tunnel that port doesn't
  exist, so the app can't complete the secure connection, and the server
  deliberately refuses access codes over plain HTTP when HTTPS is available.
- The server rejects unknown host names (DNS-rebinding protection) unless you
  list them in `allowed_hosts`, which you should not do for a public name.

Use **Tailscale** (Option 1) or **WireGuard** (Option 2) instead: they keep
the server private and the app works unchanged.

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

3. **In Lumina Gallery app**, connect to:
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
| ~~Cloudflare Tunnel~~ | Not supported (public exposure) | — | — | — |
| **SSH Tunnel** | Technical | Yes (TCP 22) | Free | Good |

## Security Reminders

- **Never** expose Lumina Gallery Server directly to the internet without a VPN/tunnel
- **Never** use port forwarding for the server ports (8500/8543) — use a VPN instead
- **Always** use a strong, unique access code — **at least 12 characters** with a lowercase letter, an uppercase letter, a number, and a special character (the server enforces this and refuses to start with a weaker code)
- **Keep** your server software updated
- Consider using a **firewall** to restrict which IPs can access the server
