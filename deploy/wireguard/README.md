# WireGuard quick-config templates

# ULTRA (server) - edit values and run on the Ultra host as /etc/wireguard/wg0.conf
# Replace <ULTRA_PRIVATE_KEY> and <CLOUD_PUBLIC_KEY> and the external interface (eth0) as needed.

[Interface]
PrivateKey = <ULTRA_PRIVATE_KEY>
Address = 10.0.0.1/24
ListenPort = 51820
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE

[Peer]
# Cloudhost (peer)
PublicKey = <CLOUD_PUBLIC_KEY>
AllowedIPs = 10.0.0.2/32

# CLOUDHOST (peer) - run on telegram.clodhost.com as /etc/wireguard/wg0.conf
# Replace <CLOUD_PRIVATE_KEY> and <ULTRA_PUBLIC_KEY> and set Endpoint to Ultra's public host:port

[Interface]
PrivateKey = <CLOUD_PRIVATE_KEY>
Address = 10.0.0.2/24

[Peer]
PublicKey = <ULTRA_PUBLIC_KEY>
Endpoint = <ultra.public.hostname>:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25

# Notes:
# - AllowedIPs=0.0.0.0/0 on the cloudhost peer will route all outbound traffic through Ultra. If you only
#   want to route Telegram (complex), configure more specific routing rules instead.
# - Ensure Ultra's firewall allows the WireGuard listen port and that IP forwarding / NAT is enabled.
# - After putting these files in place, enable with: sudo systemctl enable --now [email protected]
