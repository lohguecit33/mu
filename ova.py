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
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeElapsedColumn
from termcolor import colored

# =====================
# Roblox MuMu Auto Rejoin - FINAL
# Semua fitur digabung: auto rejoin lengkap, progress bar, per-instance join,
# block/unblock, config (game_id + private_link), device sorting, presence check
# =====================

console = Console()
status_lock = threading.Lock()
device_status = {}  # device_id -> {apk, presence, username, userid}

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ADB_PATH = os.path.join(SCRIPT_DIR, "adb", "adb.exe")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
COOKIES_FILE = os.path.join(SCRIPT_DIR, "cookie.txt")

# Defaults
DEFAULT_CONFIG = {"game_id": 2753915549, "private_link": "", "check_interval": 60}

# --------------------- Utility: config / cookies ---------------------

def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)


def load_cookies():
    # returns list of cookie strings
    if not os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write("# masukkan .ROBLOSECURITY cookie setiap baris tanpa teks tambahan
")
        console.print(colored("cookie.txt dibuat, isi dengan .ROBLOSECURITY tiap akun.", "yellow"))
        return []
    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
    return lines

# --------------------- Roblox API helpers ---------------------

def get_user_from_cookie(cookie):
    try:
        r = requests.get("https://users.roblox.com/v1/users/authenticated",
                         cookies={'.ROBLOSECURITY': cookie}, timeout=6)
        r.raise_for_status()
        js = r.json()
        return js.get('id'), js.get('name')
    except Exception:
        return None, None


def get_presence(cookie, user_id):
    try:
        headers = {"Cookie": f".ROBLOSECURITY={cookie}", "Content-Type": "application/json"}
        data = {"userIds": [user_id]}
        resp = requests.post("https://presence.roblox.com/v1/presence/users", headers=headers, json=data, timeout=8)
        if resp.status_code == 200:
            info = resp.json().get('userPresences', [{}])[0]
            presence_map = {0: "Offline", 1: "Online", 2: "In-Game"}
            return presence_map.get(info.get('userPresenceType'), 'Unknown')
        return 'Error'
    except Exception:
        return 'Error'

# --------------------- ADB / device helpers ---------------------

def adb(cmd_args):
    cmd = [ADB_PATH] + cmd_args
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return out
    except subprocess.CalledProcessError as e:
        return e.output
    except FileNotFoundError:
        raise RuntimeError(f"adb not found at: {ADB_PATH}")


def get_connected_devices():
    try:
        out = adb(["devices"])
        lines = out.strip().splitlines()[1:]
        devices = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == 'device' and parts[0].startswith('127.0.0.1:'):
                devices.append(parts[0])
        # sort by port number after colon
        devices.sort(key=lambda d: int(d.split(':')[-1]))
        return devices
    except Exception:
        return []


def is_roblox_running(device):
    try:
        out = adb(["-s", device, "shell", "pidof", "com.roblox.client"]) or ""
        return bool(out.strip())
    except Exception:
        return False


def force_stop_roblox(device):
    try:
        adb(["-s", device, "shell", "am", "force-stop", "com.roblox.client"])
    except Exception:
        pass


def start_roblox_with_url(device, url):
    try:
        adb(["-s", device, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url,
             "-n", "com.roblox.client/com.roblox.client.ActivityProtocolLaunch"])
    except Exception:
        pass

# --------------------- Auto-rejoin worker & manager ---------------------

def device_worker(device_id, cookie, config):
    """Background loop per-device. Mengecek APK, presence dan auto-join bila perlu."""
    online_count = 0
    while True:
        apk_open = is_roblox_running(device_id)
        apk_status = "Terbuka" if apk_open else "Tertutup"

        presence = "Unknown"
        if cookie and apk_open:
            uid, _ = get_user_from_cookie(cookie)
            if uid:
                presence = get_presence(cookie, uid)
            else:
                presence = "Error"

        # update shared status
        with status_lock:
            device_status[device_id] = {
                "apk": apk_status,
                "presence": presence,
                "username": device_status.get(device_id, {}).get('username', '-'),
                "userid": device_status.get(device_id, {}).get('userid', '-')
            }

        # rejoin logic
        if apk_status == "Tertutup" or presence == "Offline":
            online_count = 0
            force_stop_roblox(device_id)
            time.sleep(1)
            # perform join
            if config.get('private_link'):
                start_roblox_with_url(device_id, config['private_link'])
            else:
                start_roblox_with_url(device_id, f"roblox://placeId={config.get('game_id')}")
        elif presence == "Online":
            online_count += 1
            # if stays Online for too long (no In-Game), restart
            if online_count >= 4:
                online_count = 0
                force_stop_roblox(device_id)
                time.sleep(1)
                if config.get('private_link'):
                    start_roblox_with_url(device_id, config['private_link'])
                else:
                    start_roblox_with_url(device_id, f"roblox://placeId={config.get('game_id')}")
        else:
            online_count = 0

        time.sleep(config.get('check_interval', 60))


def check_and_connect_devices(config, users):
    """Start workers for all connected devices and map cookies to devices by index.
    Returns list of threads started.
    """
    devices = get_connected_devices()
    threads = []
    for idx, dev in enumerate(devices):
        cookie = users[idx]['cookie'] if idx < len(users) else None
        # attach username/userid if available
        if cookie:
            uid, uname = get_user_from_cookie(cookie)
        else:
            uid, uname = None, None
        with status_lock:
            device_status[dev] = {
                'apk': '-', 'presence': '-', 'username': uname or '-', 'userid': str(uid) if uid else '-'
            }
        t = threading.Thread(target=device_worker, args=(dev, cookie, config), daemon=True)
        t.start()
        threads.append(t)
    return devices, threads

# --------------------- Progress UI: monitor with progress bar ---------------------

def show_monitor_loop(config, users):
    """Live monitor showing table and a rejoin progress per device."""
    devices, _ = check_and_connect_devices(config, users)

    # create a progress bar just to show activity when we trigger joins
    progress = Progress(TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), TimeElapsedColumn())
    task_map = {}

    with Live(refresh_per_second=2, console=console) as live:
        # initialize tasks
        for d in devices:
            task_map[d] = progress.add_task(f"{d}", total=100, visible=False)

        while True:
            table = Table(title="OVA Rejoin Monitor", expand=True)
            table.add_column("DEVICE", style="cyan")
            table.add_column("USERNAME", style="green")
            table.add_column("USERID", style="magenta")
            table.add_column("APK", style="yellow")
            table.add_column("PRESENCE", style="red")

            with status_lock:
                # ensure ordering by port
                devs = sorted(device_status.keys(), key=lambda d: int(d.split(':')[-1]))
                for d in devs:
                    info = device_status.get(d, {})
                    table.add_row(d, info.get('username', '-'), str(info.get('userid', '-')), info.get('apk', '-'), info.get('presence', '-'))

            # combine table + progress
            live.update(table)
            time.sleep(1)

# --------------------- Block / Unblock logic (from user's script) ---------------------

def get_csrf_token(session, cookie):
    resp = session.post('https://auth.roblox.com/v2/login', cookies={'.ROBLOSECURITY': cookie}, timeout=6)
    return resp.headers.get('x-csrf-token')


def generate_rbx_event_tracker():
    return (
        f"CreateDate={datetime.now(timezone.utc).strftime('%m/%d/%Y %H:%M:%S')}&"
        f"rbxid={random.randint(100000000,999999999)}&"
        f"browserid={random.randint(10**15,10**16 - 1)}"
    )


def block_or_unblock(session, blocker_cookie, csrf_token, blocker_name, target_id, action, results):
    target_name = get_username_by_cookieless_id(target_id)
    rbx_event = generate_rbx_event_tracker()
    url = f'https://apis.roblox.com/user-blocking-api/v1/users/{target_id}/{action}-user'
    try:
        resp = session.post(url, cookies={'.ROBLOSECURITY': blocker_cookie, 'RBXEventTrackerV2': rbx_event},
                            headers={'X-CSRF-TOKEN': csrf_token, 'Origin': 'https://www.roblox.com', 'Referer': 'https://www.roblox.com/', 'User-Agent': 'Mozilla/5.0', 'Content-Length': '0'}, timeout=6)
        if resp.status_code == 200:
            status_text = "Berhasil block" if action == "block" else "Berhasil unblock"
        elif resp.status_code == 400:
            status_text = "Sudah diblock" if action == "block" else "Tidak diblock"
        else:
            status_text = f"Gagal ({resp.status_code})"
        with status_lock:
            results.append([blocker_name, target_name, status_text])
    except Exception as e:
        with status_lock:
            results.append([blocker_name, target_name, f"Error {e}"])


def process_block_action(users, action):
    results = []
    console.print(f"
🚀 Memulai proses saling {action}...
")
    start = datetime.now()

    threads = []
    sessions = []
    for user in users:
        blocker_cookie = user['cookie']
        blocker_name = user['name']
        blocker_id = user['id']
        targets = [u['id'] for u in users if u['id'] != blocker_id]
        session = requests.Session()
        sessions.append(session)
        csrf = get_csrf_token(session, blocker_cookie)
        if not csrf:
            console.print(f"[red]✘ CSRF token fetch gagal untuk {blocker_name}[/red]")
            session.close()
            continue
        for tid in targets:
            t = threading.Thread(target=block_or_unblock, args=(session, blocker_cookie, csrf, blocker_name, tid, action, results))
            t.start()
            threads.append(t)

    for t in threads:
        t.join()
    for s in sessions:
        try:
            s.close()
        except:
            pass

    table = Table(title=f"Hasil {action.capitalize()}")
    table.add_column("Akun", style="cyan", justify="center")
    table.add_column("Target", style="magenta", justify="center")
    table.add_column("Status", style="green", justify="center")
    for r in results:
        table.add_row(*r)
    console.print(table)
    elapsed = (datetime.now() - start).total_seconds()
    console.print(f"
⏱️ Selesai dalam {elapsed:.2f} detik")
    input("
Tekan Enter atau 0 untuk kembali ke menu...")


def get_username_by_cookieless_id(user_id):
    try:
        r = requests.get(f"https://users.roblox.com/v1/users/{user_id}", timeout=6)
        r.raise_for_status()
        return r.json().get('name')
    except Exception:
        return str(user_id)

# --------------------- Menu actions: join private link all/per-instance ---------------------

def validate_private_link(link):
    # basic validation: must contain privateServerLinkCode= and be on www.roblox.com
    if not link:
        return False
    if not link.startswith('https://'):
        return False
    if 'www.roblox.com' not in link:
        return False
    if 'privateServerLinkCode=' not in link:
        return False
    return True


def join_private_all(config, users):
    if not config.get('private_link'):
        console.print('[red]Private link belum diatur di config![/red]')
        return
    devices = get_connected_devices()
    for i, d in enumerate(devices):
        if i < len(users):
            start_roblox_with_url(d, config['private_link'])
    console.print('[green]Semua instance diarahkan ke Private Link![/green]')
    input('
Tekan Enter untuk kembali ke menu...')


def join_private_one(config, users):
    if not config.get('private_link'):
        console.print('[red]Private link belum diatur di config![/red]')
        return
    devices = get_connected_devices()
    if not devices:
        console.print('[red]Tidak ada device yang terhubung![/red]')
        return
    console.print('
Daftar device:')
    for idx, d in enumerate(devices):
        console.print(f"{idx+1}. {d}")
    sel = input('Pilih nomor device (atau Enter untuk batal): ').strip()
    if not sel or not sel.isdigit():
        return
    sel_idx = int(sel) - 1
    if not (0 <= sel_idx < len(devices)):
        console.print('[red]Pilihan tidak valid![/red]')
        return
    dev = devices[sel_idx]
    start_roblox_with_url(dev, config['private_link'])
    console.print(f"[green]Device {dev} diarahkan ke Private Link![/green]")
    input('
Tekan Enter untuk kembali ke menu...')

# --------------------- Setup Config (menu) ---------------------

def setup_config_menu():
    cfg = load_config()
    console.print('
[bold]Setup Config[/bold]')
    console.print(f"Game ID sekarang: {cfg.get('game_id')}")
    console.print(f"Private Link sekarang: {cfg.get('private_link') or '-'}")
    new_game = input('Masukkan Game ID baru (atau Enter untuk batal): ').strip()
    if new_game:
        try:
            cfg['game_id'] = int(new_game)
        except ValueError:
            console.print('[red]Game ID tidak valid![/red]')
    new_pl = input('Masukkan Private Link (atau Enter untuk batal): ').strip()
    if new_pl:
        if validate_private_link(new_pl):
            cfg['private_link'] = new_pl
        else:
            console.print('[red]Private link tidak valid (harus dari www.roblox.com dan mengandung privateServerLinkCode)[/red]')
    save_config(cfg)
    console.print('[green]Config tersimpan![/green]')
    input('
Tekan Enter untuk kembali ke menu...')

# --------------------- Main menu ---------------------

def main():
    # prepare users from cookie.txt
    cookies = load_cookies()
    users = []
    for c in cookies:
        uid, uname = get_user_from_cookie(c)
        if uid:
            users.append({'cookie': c, 'id': uid, 'name': uname})
            console.print(f"✔ {uname} (ID: {uid}) terhubung")
        else:
            console.print('[red]✘ Gagal membaca cookie atau cookie invalid[/red]')

    cfg = load_config()

    while True:
        console.print('
[bold cyan]=== MENU UTAMA ===[/bold cyan]')
        console.print('1. Auto Rejoin (monitor + auto logic)')
        console.print('2. Setup Config (GameId & PrivateLink)')
        console.print('3. Block/Unblock Akun')
        console.print('4. Join Private Link (semua instance)')
        console.print('5. Join Private Link (per instance)')
        console.print('0. Keluar')

        choice = input('Masukkan pilihan: ').strip()

        if choice == '1':
            console.print('[cyan]Memulai Auto Rejoin... Tekan Ctrl+C untuk berhenti dan kembali ke menu.[/cyan]')
            # start adb server
            try:
                subprocess.run([ADB_PATH, 'start-server'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                console.print(f"[red]Gagal menjalankan adb di: {ADB_PATH}[/red]")
            try:
                show_monitor_loop(cfg, users)
            except KeyboardInterrupt:
                console.print('
[bold yellow]Auto Rejoin dihentikan. Kembali ke menu...[/bold yellow]')
                time.sleep(0.8)

        elif choice == '2':
            setup_config_menu()
            cfg = load_config()

        elif choice == '3':
            if not users:
                console.print('[red]Tidak ada akun yang valid di cookie.txt[/red]')
                input('
Tekan Enter untuk kembali ke menu...')
            else:
                console.print('
[bold]Menu Block/Unblock:[/bold]')
                console.print('1. Block semua akun')
                console.print('2. Unblock semua akun')
                console.print('0. Kembali')
                sub = input('Pilih: ').strip()
                if sub == '1':
                    process_block_action(users, 'block')
                elif sub == '2':
                    process_block_action(users, 'unblock')

        elif choice == '4':
            join_private_all(cfg, users)

        elif choice == '5':
            join_private_one(cfg, users)

        elif choice == '0' or choice == '':
            console.print('
👋 Keluar...')
            break

        else:
            console.print('[red]Pilihan tidak valid![/red]')


if __name__ == '__main__':
    main()
