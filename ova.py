import os
import sys
import subprocess
import threading
import time
import json
import requests
import random
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from termcolor import colored

# ======================================================
# Global Objects & Constants
# ======================================================
console = Console()
status_lock = threading.Lock()
device_status = {}

CONFIG_FILE = "config.json"
COOKIES_FILE = "cookies.txt"

# Lokasi adb di dalam EXE (PyInstaller extract folder) atau di folder script
if getattr(sys, 'frozen', False):
    # Kalau sudah jadi EXE
    SCRIPT_DIR = sys._MEIPASS  # type: ignore
else:
    # Kalau masih berupa script .py
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

ADB_PATH = os.path.join(SCRIPT_DIR, "adb", "adb.exe")

# ======================================================
# Fungsi utilitas & Config (dari Script Pertama)
# ======================================================
def load_config():
    """Buat config.json kalau belum ada, lalu load."""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "game_id": 2753915549,
            "private_link": "",
            "check_interval": 60,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

def load_cookies():
    """Load cookies.txt (satu baris = satu akun). Bila belum ada, buat contoh."""
    if not os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write("ISI_COOKIE_DISINI\n")
        print(colored("File cookies.txt dibuat, isi dengan .ROBLOSECURITY tiap akun.", "yellow"))
        return []
    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def update_table():
    """Render tabel realtime status device."""
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
# Fungsi Roblox Presence & Join (dari Script Pertama)
# ======================================================

def get_presence(cookie, user_id):
    """Cek presence Roblox via API."""
    headers = {
        "Cookie": f".ROBLOSECURITY={cookie}",
        "Content-Type": "application/json",
    }
    data = {"userIds": [user_id]}
    try:
        resp = requests.post(
            "https://presence.roblox.com/v1/presence/users",
            headers=headers,
            json=data,
            timeout=10,
        )
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
            [
                ADB_PATH,
                "-s",
                device_id,
                "shell",
                "am",
                "start",
                "-n",
                "com.roblox.client/com.roblox.client.startup.ActivitySplash",
                "-d",
                private_link,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(10)
        subprocess.run(
            [
                ADB_PATH,
                "-s",
                device_id,
                "shell",
                "am",
                "start",
                "-n",
                "com.roblox.client/com.roblox.client.ActivityProtocolLaunch",
                "-d",
                private_link,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(8)
        print(colored(f"Private link dijalankan di {device_id}.", "green"))
    except Exception as e:
        print(colored(f"Gagal Private Server {device_id}: {e}", "red"))

def start_default_server(device_id, game_id):
    try:
        game_url = f"roblox://placeID={game_id}"
        subprocess.run(
            [
                ADB_PATH,
                "-s",
                device_id,
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                game_url,
                "-n",
                "com.roblox.client/com.roblox.client.ActivityProtocolLaunch",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(10)
        print(colored(f"Membuka game {game_url} di {device_id}.", "green"))
    except Exception as e:
        print(colored(f"Gagal Default Server {device_id}: {e}", "red"))

def auto_join_game(device_id, game_id, private_link):
    if private_link:
        start_private_server(device_id, private_link)
    else:
        start_default_server(device_id, game_id)

# ======================================================
# Worker Per-Device (dari Script Pertama)
# ======================================================

def device_worker(device_id, cookie, config):
    online_count = 0  # counter untuk deteksi berlarut Online tanpa In-Game
    while True:
        # Cek apk (proses Roblox)
        proc = subprocess.run(
            [ADB_PATH, "-s", device_id, "shell", "pidof", "com.roblox.client"],
            capture_output=True,
            text=True,
        )
        apk_status = "Terbuka" if proc.stdout.strip() else "Tertutup"

        # Default presence
        presence = "Unknown"
        if cookie and apk_status == "Terbuka":
            try:
                r = requests.get(
                    "https://users.roblox.com/v1/users/authenticated",
                    headers={"Cookie": f".ROBLOSECURITY={cookie}"},
                    timeout=10,
                )
                if r.status_code == 200:
                    user_id = r.json()["id"]
                    presence = get_presence(cookie, user_id)
                else:
                    presence = "Error"
            except Exception:
                presence = "Error"

        with status_lock:
            device_status[device_id] = {"apk": apk_status, "presence": presence}
        update_table()

        # Logika rejoin
        if apk_status == "Tertutup" or presence == "Offline":
            online_count = 0  # Reset counter
            subprocess.run(
                [
                    ADB_PATH,
                    "-s",
                    device_id,
                    "shell",
                    "am",
                    "force-stop",
                    "com.roblox.client",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)
            auto_join_game(device_id, config["game_id"], config["private_link"])
        elif presence == "Online":
            online_count += 1
            if online_count >= 4:
                online_count = 0  # Reset counter setelah restart
                subprocess.run(
                    [
                        ADB_PATH,
                        "-s",
                        device_id,
                        "shell",
                        "am",
                        "force-stop",
                        "com.roblox.client",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(2)
                auto_join_game(device_id, config["game_id"], config["private_link"])
        else:
            online_count = 0  # Reset jika presence berubah

        time.sleep(config.get("check_interval", 60))

# ======================================================
# Wrapper untuk Menjalankan Auto Rejoin (Menu 1)
# ======================================================

def start_auto_rejoin():
    config = load_config()

    # Set interval ke 2 menit (120 detik) sesuai instruksi script awal
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
        input("\nTekan Enter untuk kembali ke menu...")
        return

    print(colored(f"Deteksi {len(devices)} emulator.", "green"))

    cookies = load_cookies()

    # Jalankan worker per device
    for idx, device in enumerate(devices):
        cookie = cookies[idx] if idx < len(cookies) else None
        threading.Thread(
            target=device_worker, args=(device, cookie, config), daemon=True
        ).start()

    console.print("\n[bold green]Auto Rejoin berjalan. Tekan Ctrl+C untuk berhenti dan kembali ke menu.[/bold green]")

    try:
        # Loop agar script tidak mati
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Dihentikan oleh user. Kembali ke menu...[/bold yellow]")
        time.sleep(1)

# ======================================================
# Bagian Block / Unblock (dari Script Kedua)
# ======================================================

def get_user_id_from_cookie(cookie):
    """Return (id, name) dari cookie .ROBLOSECURITY."""
    try:
        resp = requests.get(
            "https://users.roblox.com/v1/users/authenticated",
            cookies={".ROBLOSECURITY": cookie},
            timeout=5,
        )
        resp.raise_for_status()
        user = resp.json()
        return user.get("id"), user.get("name")
    except Exception:
        return None, None

def get_username_by_id(user_id):
    try:
        resp = requests.get(f"https://users.roblox.com/v1/users/{user_id}", timeout=5)
        resp.raise_for_status()
        return resp.json().get("name") or "Unknown"
    except Exception:
        return "Unknown"

def get_csrf_token(session: requests.Session, cookie: str):
    resp = session.post(
        "https://auth.roblox.com/v2/login",
        cookies={".ROBLOSECURITY": cookie},
        timeout=5,
    )
    return resp.headers.get("x-csrf-token")

def generate_rbx_event_tracker():
    return (
        f"CreateDate={datetime.now(timezone.utc).strftime('%m/%d/%Y %H:%M:%S')}&"
        f"rbxid={random.randint(100000000, 999999999)}&"
        f"browserid={random.randint(10**15, 10**16 - 1)}"
    )

def block_or_unblock(session: requests.Session, blocker_cookie: str, csrf_token: str, blocker_name: str, target_id: int, action: str, results: list, thread_lock: threading.Lock):
    target_name = get_username_by_id(target_id)
    rbx_event = generate_rbx_event_tracker()
    url = f"https://apis.roblox.com/user-blocking-api/v1/users/{target_id}/{action}-user"

    try:
        resp = session.post(
            url,
            cookies={
                ".ROBLOSECURITY": blocker_cookie,
                "RBXEventTrackerV2": rbx_event,
            },
            headers={
                "X-CSRF-TOKEN": csrf_token,
                "Origin": "https://www.roblox.com",
                "Referer": "https://www.roblox.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "application/json, text/plain, */*",
                "Content-Length": "0",
            },
            timeout=5,
        )
        with thread_lock:
            if resp.status_code == 200:
                status_text = "Berhasil block" if action == "block" else "Berhasil unblock"
            elif resp.status_code == 400:
                status_text = "Sudah diblock" if action == "block" else "Tidak diblock"
            else:
                status_text = f"Gagal ({resp.status_code})"
            results.append([blocker_name, target_name, status_text])

    except Exception as e:
        with thread_lock:
            results.append([blocker_name, target_name, f"Error {e}"])

def process_action(users: list, action: str):
    results = []
    console.print(f"\n🚀 Memulai proses saling {action}...\n")
    start = datetime.now()

    threads = []
    for user in users:
        blocker_cookie = user["cookie"]
        blocker_name = user["name"]
        blocker_id = user["id"]
        targets = [u["id"] for u in users if u["id"] != blocker_id]

        session = requests.Session()
        csrf = get_csrf_token(session, blocker_cookie)
        if not csrf:
            console.print(f"[red]✘ CSRF token fetch gagal untuk {blocker_name}[/red]")
            session.close()
            continue

        for tid in targets:
            t = threading.Thread(
                target=block_or_unblock,
                args=(
                    session,
                    blocker_cookie,
                    csrf,
                    blocker_name,
                    tid,
                    action,
                    results,
                    status_lock,
                ),
            )
            t.start()
            threads.append((t, session))

    # Join semua thread
    for t, _ in threads:
        t.join()

    # Tutup semua session
    for _, s in threads:
        try:
            s.close()
        except Exception:
            pass

    # tampilkan tabel hasil
    table = Table(title=f"Hasil {action.capitalize()}")
    table.add_column("Akun", style="cyan", justify="center")
    table.add_column("Target", style="magenta", justify="center")
    table.add_column("Status", style="green", justify="center")

    for row in results:
        table.add_row(*row)

    console.print(table)

    elapsed = (datetime.now() - start).total_seconds()
    console.print(f"\n⏱️ Selesai dalam {elapsed:.2f} detik")

    input("\nTekan Enter atau 0 untuk kembali ke menu...")

def start_block_menu():
    if not os.path.exists(COOKIES_FILE):
        print(colored("cookies.txt tidak ditemukan!", "red"))
        input("\nTekan Enter untuk kembali ke menu...")
        return

    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        raw_cookies = [line.strip() for line in f if line.strip()]

    users = []
    console.print("🔗 Membaca akun dari cookies.txt\n")
    for cookie in raw_cookies:
        uid, uname = get_user_id_from_cookie(cookie)
        if uid:
            users.append({"cookie": cookie, "id": uid, "name": uname})
            console.print(f"✔ {uname} (ID: {uid}) terhubung")
        else:
            console.print(f"[red]✘ Gagal membaca cookie[/red]")

    if not users:
        console.print("[red]Tidak ada akun valid![/red]")
        input("\nTekan Enter untuk kembali ke menu...")
        return

    while True:
        console.print("\n[bold]Menu Block/Unblock:[/bold]")
        console.print("1. Block semua akun")
        console.print("2. Unblock semua akun")
        console.print("0. Kembali")
        choice = input("Masukkan pilihan (0/1/2): ").strip()

        if choice == "1":
            process_action(users, "block")
        elif choice == "2":
            process_action(users, "unblock")
        elif choice == "0" or choice == "":
            break
        else:
            console.print("[red]Pilihan tidak valid![/red]")

# ======================================================
# Setup Config (Menu 2)
# ======================================================

def setup_config():
    config = load_config()
    console.print("\n[bold]Setup Config[/bold]")
    console.print(f"Game ID sekarang: {config['game_id']}")
    new_id = input("Masukkan Game ID baru (atau Enter untuk batal): ").strip()
    if new_id:
        try:
            config["game_id"] = int(new_id)
            save_config(config)
            console.print("[green]✔ Config berhasil diperbarui![/green]")
        except Exception:
            console.print("[red]✘ Game ID tidak valid![/red]")
    # otomatis reload config selesai dan kembali ke menu
    input("Tekan Enter untuk kembali ke menu...")

# ======================================================
# Menu Utama
# ======================================================

def main_menu():
    while True:
        console.print("\n[bold cyan]=== MENU UTAMA ===[/bold cyan]")
        console.print("1. Auto Rejoin")
        console.print("2. Setup Config")
        console.print("3. Block/Unblock Akun")
        console.print("0. Keluar")
        choice = input("Masukkan pilihan: ").strip()

        if choice == "1":
            start_auto_rejoin()
        elif choice == "2":
            setup_config()
        elif choice == "3":
            start_block_menu()
        elif choice == "0" or choice == "":
            console.print("\n👋 Keluar...")
            break
        else:
            console.print("[red]Pilihan tidak valid![/red]")

# ======================================================
# Entry Point
# ======================================================
if __name__ == "__main__":
    main_menu()
