"""
Fast CDP tab monitor — monitors a known Chrome debug port every 0.5s.
Also checks common ports around it. Writes to tab_monitor.log.
"""
import urllib.request, json, time, datetime, socket

LOG_FILE = "tab_monitor.log"
# These are the ports browser_factory commonly picks (random 10000-20000)
# We'll try to find Chrome by checking a few candidate ports
CANDIDATE_PORTS = [15117, 9222, 9223, 9224, 9225, 9226, 9227, 9228, 9229, 9230]


def check_port_open(port, timeout=0.2):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(('127.0.0.1', port)) == 0


def find_chrome_port():
    """Try candidate ports, then brute-force scan 10000-20000 in chunks."""
    for port in CANDIDATE_PORTS:
        if check_port_open(port, 0.1):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=0.5
                ) as r:
                    ver = json.loads(r.read().decode())
                    if "Chrom" in ver.get("Browser", ""):
                        return port
            except Exception:
                pass

    # Brute force in chunks of 500
    for base in range(10000, 20001, 500):
        chunk = range(base, min(base + 500, 20001))
        open_ports = [p for p in chunk if check_port_open(p, 0.02)]
        for port in open_ports:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=0.5
                ) as r:
                    ver = json.loads(r.read().decode())
                    if "Chrom" in ver.get("Browser", ""):
                        CANDIDATE_PORTS.insert(0, port)  # cache it first
                        return port
            except Exception:
                pass
    return None


def get_tabs(port):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json", timeout=0.5
        ) as r:
            targets = json.loads(r.read().decode())
        return [t for t in targets if t.get("type") == "page"]
    except Exception:
        return None


def main():
    print(f"[TabMonitor] Starting — writing to {LOG_FILE}")
    current_port = None
    iteration = 0

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"TabMonitor started at {datetime.datetime.now()}\n")
        f.flush()

        while True:
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            iteration += 1

            # (Re-)discover port every 20 iters or if we lost it
            if current_port is None or iteration % 20 == 0:
                found = find_chrome_port()
                if found:
                    if found != current_port:
                        msg = f"[{ts}] Discovered Chrome CDP on port {found}"
                        f.write(msg + "\n"); f.flush(); print(msg)
                    current_port = found
                else:
                    if current_port is not None:
                        msg = f"[{ts}] Lost Chrome — scanning..."
                        f.write(msg + "\n"); f.flush(); print(msg)
                    current_port = None

            if current_port:
                tabs = get_tabs(current_port)
                if tabs is not None:
                    line = f"[{ts}] PORT={current_port} TABS={len(tabs)}"
                    for t in tabs:
                        url = t.get("url", "")
                        tid = t.get("id", "")[:8]
                        line += f"\n         [{tid}] {url[:120]}"
                    f.write(line + "\n"); f.flush()
                    print(line)
                else:
                    current_port = None  # port closed, force re-scan

            time.sleep(0.5)


if __name__ == "__main__":
    main()
