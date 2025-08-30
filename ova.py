import os
import subprocess
import threading
import time
import json
import requests
from rich.console import Console
from rich.table import Table
from termcolor import colored

# Lokasi adb di folder script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADB_PATH = os.path.join(SCRIPT_DIR, "adb", "adb.exe")
CONFIG_FILE = "config.json"
COOKIES_FILE = "cookies.txt"

console = Console()
status_lock = threading.Lock()
device_status = {}

# ======================================================
# Fungsi utilitas
# ======================================================
def load_config():
    """Buat config.json kalau belum ada"""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "game_id": 2753915549,
            "private_link": "",
            "check_interval": 60
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def load_cookies():
    """Load cookies.txt (satu baris = satu akun)"""
    if not os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, "w") as f:
            f.write("ISI_COOKIE_DISINI\n")
        print(colored("File cookies.txt dibuat, isi dengan .ROBLOSECURITY tiap akun.", "yellow"))
        return []
    with open(COOKIES_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]

def update_table():
    """Render tabel realtime"""
    table = Table(title="OVA Rejoin Roblox MuMu", expand=True)
    table.add_column("DEVICE", style="cyan", no_wrap=True)
    table.add_column("APK", style="green")
    table.add_column("PRESENCE", style="magenta")

    with status_lock:
        for dev, info in device_status.items():
            table.add_row(dev, info.get("apk", "-"), info.get("presence", "-"))

    console.clear()
    console.print(table)

# ======================================================
# Fungsi Roblox
# ======================================================
def get_presence(cookie, user_id):
    """Cek presence Roblox via API"""
    headers = {
        "Cookie": f".ROBLOSECURITY={cookie}",
        "Content-Type": "application/json"
    }
    data = {"userIds": [user_id]}
    try:
        resp = requests.post("https://presence.roblox.com/v1/presence/users",
                             headers=headers, json=data, timeout=10)
        if resp.status_code == 200:
            info = resp.json()["userPresences"][0]
            presence_map = {0: "Offline", 1: "Online", 2: "In-Game"}
            return presence_map.get(info["userPresenceType"], "Unknown")
        else:
            return "Error"
    except Exception as e:
        return f"Error {e}"

def start_private_server(device_id, private_link):
    try:
        subprocess.run(
            [ADB_PATH, '-s', device_id, 'shell', 'am', 'start',
             '-n', 'com.roblox.client/com.roblox.client.startup.ActivitySplash', '-d', private_link],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(10)
        subprocess.run(
            [ADB_PATH, '-s', device_id, 'shell', 'am', 'start',
             '-n', 'com.roblox.client/com.roblox.client.ActivityProtocolLaunch', '-d', private_link],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(8)
        print(colored(f"Private link dijalankan di {device_id}.", "green"))
    except Exception as e:
        print(colored(f"Gagal Private Server {device_id}: {e}", "red"))

def start_default_server(device_id, game_id):
    try:
        game_url = f"roblox://placeID={game_id}"
        subprocess.run(
            [ADB_PATH, '-s', device_id, 'shell', 'am', 'start',
             '-a', 'android.intent.action.VIEW', '-d', game_url,
             '-n', 'com.roblox.client/com.roblox.client.ActivityProtocolLaunch'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(10)
        print(colored(f"Membuka game {game_url} di {device_id}.", 'green'))
    except Exception as e:
        print(colored(f"Gagal Default Server {device_id}: {e}", 'red'))

def auto_join_game(device_id, game_id, private_link):
    if private_link:
        start_private_server(device_id, private_link)
    else:
        start_default_server(device_id, game_id)

# ======================================================
# Fungsi Device Worker
# ======================================================
def device_worker(device_id, cookie, config):
    online_count = 0  # Tambahkan counter
    while True:
        # Cek apk (proses Roblox)
        proc = subprocess.run(
            [ADB_PATH, "-s", device_id, "shell", "pidof", "com.roblox.client"],
            capture_output=True, text=True
        )
        apk_status = "Terbuka" if proc.stdout.strip() else "Tertutup"

        # Default presence
        presence = "Unknown"
        if cookie and apk_status == "Terbuka":
            try:
                r = requests.get("https://users.roblox.com/v1/users/authenticated",
                                 headers={"Cookie": f".ROBLOSECURITY={cookie}"},
                                 timeout=10)
                if r.status_code == 200:
                    user_id = r.json()["id"]
                    presence = get_presence(cookie, user_id)
                else:
                    presence = "Error"
            except:
                presence = "Error"

        with status_lock:
            device_status[device_id] = {"apk": apk_status, "presence": presence}
        update_table()

        # Logika rejoin
        if apk_status == "Tertutup" or presence == "Offline":
            online_count = 0  # Reset counter
            subprocess.run([ADB_PATH, "-s", device_id, "shell", "am", "force-stop", "com.roblox.client"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            auto_join_game(device_id, config["game_id"], config["private_link"])
        elif presence == "Online":
            online_count += 1
            if online_count >= 4:
                online_count = 0  # Reset counter setelah restart
                subprocess.run([ADB_PATH, "-s", device_id, "shell", "am", "force-stop", "com.roblox.client"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
                auto_join_game(device_id, config["game_id"], config["private_link"])
        else:
            online_count = 0  # Reset jika presence berubah

        time.sleep(config["check_interval"])

# ======================================================
# Main
# ======================================================
def main():
    config = load_config()
    cookies = load_cookies()

    # Set interval ke 2 menit (120 detik)
    config["check_interval"] = 120

    # Start ADB server
    subprocess.run([ADB_PATH, "start-server"], stdout=subprocess.DEVNULL)

    # Daftar device emulator, hanya yang format 127.0.0.1:xxxx
    result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
    devices = [
        line.split()[0]
        for line in result.stdout.splitlines()[1:]
        if line.strip() and "device" in line and line.split()[0].startswith("127.0.0.1:")
    ]

    if not devices:
        print(colored("Tidak ada emulator terdeteksi!", "red"))
        return

    print(colored(f"Deteksi {len(devices)} emulator.", "green"))

    # Jalankan worker per device
    for idx, device in enumerate(devices):
        cookie = cookies[idx] if idx < len(cookies) else None
        threading.Thread(target=device_worker, args=(device, cookie, config), daemon=True).start()

    # Loop agar script tidak mati
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()
