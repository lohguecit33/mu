import os
import subprocess
import threading
import time
import json
import requests
from datetime import datetime, timezone
import random
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from termcolor import colored

# Lokasi adb di folder script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADB_PATH = os.path.join(SCRIPT_DIR, "adb", "adb.exe")
CONFIG_FILE = "config.json"
COOKIES_FILE = "cookies.txt"

console = Console()
status_lock = threading.Lock()
device_status = {}
lock = threading.Lock()

# ======================================================
# Fungsi utilitas - DIPERBAIKI
def load_config():
    """Buat config.json kalau belum ada"""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "game_id": 2753915549,
            "private_link": "",
            "check_interval": 60,
            "presence_check_interval": 120,
            "max_online_checks": 3,
            "max_ports": 20
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
        
    # Validasi nilai config
    if config["check_interval"] <= 0:
        config["check_interval"] = 60
        console.log("[yellow]check_interval 0, no restart roblox[/yellow]")
    
    if config["presence_check_interval"] <= 0:
        config["presence_check_interval"] = 120
        console.log("[yellow]presence_check_interval tidak valid, menggunakan nilai default 120[/yellow]")
    
    if config["max_online_checks"] <= 0:
        config["max_online_checks"] = 3
        console.log("[yellow]max_online_checks tidak valid, menggunakan nilai default 3[/yellow]")
    
    if config["max_ports"] <= 0:
        config["max_ports"] = 20
        console.log("[yellow]max_ports tidak valid, menggunakan nilai default 20[/yellow]")
    
    return config

def load_cookies():
    """Load cookies.txt (satu baris = satu akun)"""
    if not os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, "w") as f:
            f.write("ISI_COOKIE_DISINI\n")
        console.log("[yellow]File cookies.txt dibuat, isi dengan .ROBLOSECURITY tiap akun.[/yellow]")
        return []
    with open(COOKIES_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]

def get_user_id(cookie):
    """Ambil userId dan username dari cookie"""
    url = "https://users.roblox.com/v1/users/authenticated"
    headers = {"Cookie": f".ROBLOSECURITY={cookie}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("id"), data.get("name")
    except:
        pass
    return None, None

def build_table():
    table = Table(title="OVA Rejoin Roblox MuMu", expand=True)
    table.add_column("DEVICE", style="cyan", no_wrap=True)
    table.add_column("USERNAME", style="yellow")
    table.add_column("USER ID", style="blue")
    table.add_column("APK", style="green")
    table.add_column("PRESENCE", style="magenta")

    with status_lock:
        for dev in sorted(device_status.keys(), key=lambda x: int(x.split(":")[1])):
            info = device_status[dev]
            table.add_row(
                dev,
                info.get("username", "-"),
                str(info.get("user_id", "-")),
                info.get("apk", "-"),
                info.get("presence", "-")
            )
    return table

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
            presence_map = {0: "Offline", 1: "Online", 2: "In-Game", 3: "In Studio"}
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
        console.log(f"[green]Private link dijalankan di {device_id}.[/green]")
    except Exception as e:
        console.log(f"[red]Gagal Private Server {device_id}: {e}[/red]")

def start_default_server(device_id, game_id):
    try:
        game_url = f"roblox://placeID={game_id}"
        subprocess.run(
            [ADB_PATH, '-s', device_id, 'shell', 'am', 'start',
             '-a', 'android.intent.action.VIEW', '-d', game_url,
             '-n', 'com.roblox.client/com.roblox.client.ActivityProtocolLaunch'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(10)
        console.log(f"[green]Membuka game {game_url} di {device_id}.[/green]")
    except Exception as e:
        console.log(f"[red]Gagal Default Server {device_id}: {e}[/red]")

def auto_join_game(device_id, game_id, private_link):
    if private_link:
        start_private_server(device_id, private_link)
    else:
        start_default_server(device_id, game_id)

# ======================================================
# Fungsi Device Worker
# ======================================================
def device_worker(device_id, cookie, config):
    online_count = 0
    last_presence_check = 0
    presence = "Unknown"
    
    while True:
        current_time = time.time()
        
        # Cek apk (proses Roblox)
        proc = subprocess.run(
            [ADB_PATH, "-s", device_id, "shell", "pidof", "com.roblox.client"],
            capture_output=True, text=True
        )
        apk_status = "open" if proc.stdout.strip() else "close"

        # Cek presence hanya pada interval yang ditentukan
        if cookie and apk_status == "open" and (current_time - last_presence_check) >= config["presence_check_interval"]:
            try:
                r = requests.get("https://users.roblox.com/v1/users/authenticated",
                                 headers={"Cookie": f".ROBLOSECURITY={cookie}"},
                                 timeout=10)
                if r.status_code == 200:
                    user_id = r.json()["id"]
                    presence = get_presence(cookie, user_id)
                    last_presence_check = current_time
                else:
                    presence = "Error"
            except:
                presence = "Error"
        elif apk_status == "close":
            presence = "Offline"

        with status_lock:
            if device_id not in device_status:
                device_status[device_id] = {}
            device_status[device_id].update({
                "apk": apk_status,
                "presence": presence
            })

        # Logika rejoin - hanya jika interval check > 0
        if config["check_interval"] > 0:
            if apk_status == "close" or presence == "Offline":
                online_count = 0
                subprocess.run([ADB_PATH, "-s", device_id, "shell", "am", "force-stop", "com.roblox.client"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
                auto_join_game(device_id, config["game_id"], config.get("private_link", ""))
            elif presence == "Online":
                online_count += 1
                if online_count >= config["max_online_checks"]:
                    online_count = 0
                    subprocess.run([ADB_PATH, "-s", device_id, "shell", "am", "force-stop", "com.roblox.client"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(2)
                    auto_join_game(device_id, config["game_id"], config.get("private_link", ""))
            else:
                online_count = 0

        if config["check_interval"] > 0:
            time.sleep(config["check_interval"])
        else:
            time.sleep(60)

def get_mumu_ports(config):
    """Generate port MuMu secara otomatis berdasarkan max_ports di config"""
    base_port = 16448
    step = 32
    max_ports = config.get("max_ports", 20)
    return [base_port + step * i for i in range(max_ports)]

def check_and_connect_devices(config):
    """Cek device ADB pada port MuMu, koneksi-kan jika belum terhubung, ambil yang aktif"""
    subprocess.run([ADB_PATH, "start-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ports = get_mumu_ports(config)
    port_set = set(ports)

    result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
    lines = result.stdout.splitlines()[1:]
    connected = set()
    for line in lines:
        if line.strip() and "device" in line:
            addr = line.split()[0]
            if addr.startswith("127.0.0.1:"):
                try:
                    port = int(addr.split(":")[1])
                    if port in port_set:
                        connected.add(addr)
                except ValueError:
                    continue

    for port in ports:
        addr = f"127.0.0.1:{port}"
        if addr not in connected:
            subprocess.run([ADB_PATH, "connect", addr], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
    lines = result.stdout.splitlines()[1:]
    found_devices = []
    for line in lines:
        if line.strip() and "device" in line:
            addr = line.split()[0]
            if addr.startswith("127.0.0.1:"):
                try:
                    port = int(addr.split(":")[1])
                    if port in port_set:
                        found_devices.append(addr)
                except ValueError:
                    continue
    return found_devices

# ======================================================
# Fungsi Auto Rejoin
# ======================================================
def auto_rejoin():
    try:
        config = load_config()
        cookies = load_cookies()

        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            transient=True
        )
        task = progress.add_task("[green]detect emulator...", total=100)
        with progress:
            for i in range(100):
                time.sleep(10.25)
                progress.update(task, advance=1)
            devices = check_and_connect_devices(config)

        if not devices:
            console.log("[red]no detected emulator![/red]")
            input("Tekan Enter untuk kembali ke menu...")
            return

        console.log(f"[green]Deteksi {len(devices)} emulator done.[/green]")
        console.log(f"[yellow]Config: Interval={config['check_interval']}s, Presence Check={config['presence_check_interval']}s, Max Online Checks={config['max_online_checks']}, Max Ports={config['max_ports']}[/yellow]")
        
        if config["check_interval"] == 0:
            console.log("[yellow]Mode monitor-only: no restart[/yellow]")

        for idx, device in enumerate(devices):
            cookie = cookies[idx] if idx < len(cookies) else None
            user_id, username = (None, None)
            if cookie:
                user_id, username = get_user_id(cookie)

            with status_lock:
                device_status[device] = {
                    "apk": "-",
                    "presence": "-",
                    "user_id": user_id if user_id else "-",
                    "username": username if username else "-"
                }

            threading.Thread(
                target=device_worker,
                args=(device, cookie, config),
                daemon=True
            ).start()

        console.print("[yellow]Tekan Ctrl+C untuk kembali ke menu utama[/yellow]")
        with Live(build_table(), refresh_per_second=2, console=console, screen=True) as live:
            while True:
                live.update(build_table())
                time.sleep(1)
                
    except KeyboardInterrupt:
        console.print("\n[yellow]back main menu...[/yellow]")
        time.sleep(1)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        input("tap Enter back main menu...")

# ======================================================
# Fungsi Setup Config
# ======================================================
def setup_config():
    console.print("\n[bold]Setup Konfigurasi[/bold]")
    
    # Load config yang ada
    config = load_config()
    
    console.print(f"Game ID saat ini: {config['game_id']}")
    new_game_id = input("new game id (enter not change): ").strip()
    
    if new_game_id:
        try:
            config['game_id'] = int(new_game_id)
            console.print(f"[green]Game ID change to: {config['game_id']}[/green]")
        except ValueError:
            console.print("[red]Game ID must be a number![/red]")
            return
    
    # Simpan config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)
    
    console.print("[green]Konfigurasi berhasil disimpan![/green]")
    time.sleep(1)

# ======================================================
# Fungsi Block Account
# ======================================================
def get_username_by_id(user_id):
    try:
        resp = requests.get(
            f'https://users.roblox.com/v1/users/{user_id}',
            timeout=5
        )
        resp.raise_for_status()
        return resp.json().get('name') or 'Unknown'
    except:
        return 'Unknown'

def get_csrf_token(session, cookie):
    try:
        resp = session.post(
            'https://auth.roblox.com/v2/login',
            cookies={'.ROBLOSECURITY': cookie},
            timeout=5
        )
        return resp.headers.get('x-csrf-token')
    except:
        return None

def generate_rbx_event_tracker():
    return (
        f"CreateDate={datetime.now(timezone.utc).strftime('%m/%d/%Y %H:%M:%S')}&"
        f"rbxid={random.randint(100000000,999999999)}&"
        f"browserid={random.randint(10**15,10**16 - 1)}"
    )

def block_or_unblock(session, blocker_cookie, csrf_token, blocker_name, target_id, action, results, lock):
    target_name = get_username_by_id(target_id)
    rbx_event = generate_rbx_event_tracker()
    url = f'https://apis.roblox.com/user-blocking-api/v1/users/{target_id}/{action}-user'

    try:
        resp = session.post(
            url,
            cookies={
                '.ROBLOSECURITY': blocker_cookie,
                'RBXEventTrackerV2': rbx_event
            },
            headers={
                'X-CSRF-TOKEN': csrf_token,
                'Origin': 'https://www.roblox.com',
                'Referer': 'https://www.roblox.com/',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
            },
            timeout=10
        )
        with lock:
            if resp.status_code == 200:
                status_text = "Successfully blocked" if action == "block" else "Successfully unblocked"
            elif resp.status_code == 400:
                status_text = "Already blocked" if action == "block" else "Not blocked"
            else:
                status_text = f"Failed ({resp.status_code})"

            results.append([blocker_name, target_name, status_text])

    except Exception as e:
        with lock:
            results.append([blocker_name, target_name, f"Error {e}"])

def process_action(users, action):
    results = []
    console.print(f"\n🚀 starting {action}...\n")
    start = datetime.now()

    threads = []
    for user in users:
        blocker_cookie = user['cookie']
        blocker_name = user['name']
        blocker_id = user['id']
        targets = [u['id'] for u in users if u['id'] != blocker_id]

        with requests.Session() as session:
            csrf = get_csrf_token(session, blocker_cookie)
            if not csrf:
                console.print(f"[red]✘ CSRF token fetch gagal untuk {blocker_name}[/red]")
                continue

            for tid in targets:
                t = threading.Thread(
                    target=block_or_unblock,
                    args=(session, blocker_cookie, csrf, blocker_name, tid, action, results, lock)
                )
                t.start()
                threads.append(t)

    for t in threads:
        t.join()

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

    input("\ntap Enter back main menu...")

def block_accounts():
    if not os.path.exists(COOKIES_FILE):
        console.print("[red]File cookies.txt tidak ditemukan![/red]")
        input("Tap Enter back main menu...")
        return
        
    with open(COOKIES_FILE, 'r') as f:
        raw_cookies = [line.strip() for line in f if line.strip()]

    users = []
    console.print("🔗 Membaca akun dari cookies.txt\n")
    for cookie in raw_cookies:
        uid, uname = get_user_id(cookie)
        if uid:
            users.append({'cookie': cookie, 'id': uid, 'name': uname})
            console.print(f"✔ {uname} (ID: {uid}) terhubung")
        else:
            console.print(f"[red]✘ Gagal membaca cookie[/red]")

    if not users:
        console.print("[red]Tidak ada akun yang valid![/red]")
        input("Tap Enter back main menu...")
        return
        
    while True:
        console.print("\n[bold]Menu Block/Unblock:[/bold]")
        console.print("1. Block all account")
        console.print("2. Unblock all account")
        console.print("0. back main menu")
        choice = input("selected (0/1/2): ").strip()

        if choice == "1":
            process_action(users, "block")
        elif choice == "2":
            process_action(users, "unblock")
        elif choice == "0" or choice == "":
            break
        else:
            console.print("[red]Invalid selection![/red]")

# ======================================================
# Main Menu
# ======================================================
def main_menu():
    while True:
        console.print("\n" + "="*50)
        console.print("[bold cyan]OVA ROBLOX MULTI-TOOL[/bold cyan]")
        console.print("="*50)
        console.print("1. Auto Rejoin Roblox")
        console.print("2. Setup Config (Game ID)")
        console.print("3. Block/Unblock account")
        console.print("0. Exit")
        console.print("="*50)
        
        choice = input("selected (0-3): ").strip()
        
        if choice == "1":
            auto_rejoin()
        elif choice == "2":
            setup_config()
        elif choice == "3":
            block_accounts()
        elif choice == "0":
            console.print("[green]Terima kasih! Program dihentikan.[/green]")
            break
        else:
            console.print("[red]Pilihan tidak valid! Silakan pilih 0-3.[/red]")

# ======================================================
# Main
# ======================================================
if __name__ == "__main__":
    main_menu()

