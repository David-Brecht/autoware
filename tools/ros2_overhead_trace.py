#!/usr/bin/env python3
"""Sample ROS 2 helper-node overhead and plot simple time-series metrics."""

from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class ProcSample:
    pid: int
    cpu_ticks: int
    rss_bytes: int
    cmdline: str


ROS_CANDIDATE_RE = re.compile(
    r"(ros2|rclpy|rclcpp|component_container|python|uvicorn|gunicorn|flask|fastapi|websocket|rosbridge|dds)",
    re.IGNORECASE,
)


def read_total_cpu_ticks() -> int:
    with open("/proc/stat", "r", encoding="utf-8") as f:
        fields = f.readline().split()[1:]
    return sum(int(v) for v in fields)


def iter_pids() -> Iterable[int]:
    for entry in os.scandir("/proc"):
        if entry.name.isdigit():
            yield int(entry.name)


def read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def read_comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_proc_sample(pid: int) -> ProcSample | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        parts = stat.rsplit(") ", 1)[1].split()
        utime = int(parts[11])
        stime = int(parts[12])
        rss_pages = int(parts[21])
        rss_bytes = rss_pages * os.sysconf("SC_PAGE_SIZE")
        cmdline = read_cmdline(pid) or read_comm(pid)
        return ProcSample(pid, utime + stime, rss_bytes, cmdline)
    except (OSError, IndexError, ValueError):
        return None


def find_matching_processes(
    substrings: list[str],
    regexes: list[re.Pattern[str]],
    pids: set[int],
    self_pid: int,
) -> list[ProcSample]:
    found: dict[int, ProcSample] = {}
    for pid in pids:
        if pid == self_pid:
            continue
        sample = read_proc_sample(pid)
        if sample:
            found[pid] = sample

    for pid in iter_pids():
        if pid == self_pid:
            continue
        cmdline = read_cmdline(pid) or read_comm(pid)
        if any(match in cmdline for match in substrings) or any(regex.search(cmdline) for regex in regexes):
            sample = read_proc_sample(pid)
            if sample:
                found[pid] = sample
    return list(found.values())


def list_process_candidates(self_pid: int) -> list[ProcSample]:
    candidates = []
    for pid in iter_pids():
        if pid == self_pid:
            continue
        cmdline = read_cmdline(pid) or read_comm(pid)
        if ROS_CANDIDATE_RE.search(cmdline):
            sample = read_proc_sample(pid)
            if sample:
                candidates.append(sample)
    return candidates


def read_net_dev() -> dict[str, tuple[int, int]]:
    stats = {}
    with open("/proc/net/dev", "r", encoding="utf-8") as f:
        for line in f.readlines()[2:]:
            if ":" not in line:
                continue
            iface, data = line.split(":", 1)
            fields = data.split()
            stats[iface.strip()] = (int(fields[0]), int(fields[8]))
    return stats


def choose_iface(name: str) -> str:
    stats = read_net_dev()
    if name != "auto":
        if name not in stats:
            raise SystemExit(f"Interface {name!r} not found. Available: {', '.join(sorted(stats))}")
        return name

    candidates = [iface for iface in stats if iface != "lo"]
    if not candidates:
        return "lo"
    return max(candidates, key=lambda iface: sum(stats[iface]))


def run_ros2(args: list[str], timeout: float) -> str:
    try:
        proc = subprocess.run(
            ["ros2", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


SECTION_RE = re.compile(r"^\s{2}(Subscribers|Publishers|Service Servers|Service Clients|Action Servers|Action Clients):")


def parse_node_info(text: str) -> dict[str, int]:
    counts = {
        "node_subscribers": 0,
        "node_publishers": 0,
        "node_service_servers": 0,
        "node_service_clients": 0,
        "node_action_servers": 0,
        "node_action_clients": 0,
    }
    key_by_section = {
        "Subscribers": "node_subscribers",
        "Publishers": "node_publishers",
        "Service Servers": "node_service_servers",
        "Service Clients": "node_service_clients",
        "Action Servers": "node_action_servers",
        "Action Clients": "node_action_clients",
    }
    current_key = None
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            current_key = key_by_section[match.group(1)]
            continue
        if current_key and line.startswith("    ") and ":" in line:
            counts[current_key] += 1
    return counts


def ros_count(command: list[str], timeout: float) -> int:
    output = run_ros2(command, timeout)
    if not output:
        return -1
    return len([line for line in output.splitlines() if line.strip()])


def write_plot(csv_path: Path, png_path: Path) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    rows = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return False

    t = [float(r["elapsed_s"]) for r in rows]

    fig, axes = plt.subplots(4, 1, figsize=(11, 9), sharex=True)
    axes[0].plot(t, [float(r["proc_cpu_percent"]) for r in rows], label="process CPU %")
    axes[0].set_ylabel("CPU %")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(t, [float(r["proc_rss_mb"]) for r in rows], label="RSS MB", color="tab:green")
    axes[1].set_ylabel("RSS MB")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(t, [float(r["net_rx_kib_s"]) for r in rows], label="RX KiB/s", color="tab:blue")
    axes[2].plot(t, [float(r["net_tx_kib_s"]) for r in rows], label="TX KiB/s", color="tab:orange")
    axes[2].set_ylabel("Network KiB/s")
    axes[2].legend(loc="upper right")
    axes[2].grid(True, alpha=0.3)

    axes[3].plot(t, [int(r["node_publishers"]) for r in rows], label="publishers")
    axes[3].plot(t, [int(r["node_subscribers"]) for r in rows], label="subscribers")
    axes[3].plot(t, [int(r["node_service_servers"]) for r in rows], label="service servers")
    axes[3].set_ylabel("ROS count")
    axes[3].set_xlabel("Elapsed seconds")
    axes[3].legend(loc="upper right")
    axes[3].grid(True, alpha=0.3)

    fig.suptitle("ROS 2 helper-node overhead trace")
    fig.tight_layout()
    fig.savefig(png_path, dpi=140)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", default="/ros2_info_node", help="ROS node name for `ros2 node info`.")
    parser.add_argument(
        "--match",
        action="append",
        default=[],
        help="Substring used to find related OS processes. Can be passed more than once.",
    )
    parser.add_argument(
        "--match-regex",
        action="append",
        default=[],
        help="Regex used to find related OS processes. Can be passed more than once.",
    )
    parser.add_argument("--pid", action="append", type=int, default=[], help="Explicit PID to trace. Can be repeated.")
    parser.add_argument("--list-candidates", action="store_true", help="Print likely ROS/Python/web processes and exit.")
    parser.add_argument("--duration", type=float, default=120.0, help="Trace duration in seconds.")
    parser.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds.")
    parser.add_argument("--ros-interval", type=float, default=5.0, help="How often to call ROS CLI commands.")
    parser.add_argument("--iface", default="auto", help="Network interface to sample, or auto.")
    parser.add_argument("--output-dir", default="capture/ros2_overhead", help="Directory for CSV and PNG output.")
    parser.add_argument("--no-plot", action="store_true", help="Only write CSV.")
    args = parser.parse_args()

    if args.list_candidates:
        candidates = sorted(list_process_candidates(os.getpid()), key=lambda p: p.pid)
        if not candidates:
            print("No obvious ROS/Python/web process candidates found.")
            return 1
        print("Candidate processes:")
        for sample in candidates:
            print(f"{sample.pid:>7}  rss={sample.rss_bytes / (1024 * 1024):8.1f} MiB  {sample.cmdline}")
        return 0

    regexes = []
    for pattern in args.match_regex:
        try:
            regexes.append(re.compile(pattern))
        except re.error as exc:
            raise SystemExit(f"Invalid --match-regex {pattern!r}: {exc}") from exc

    if not args.match and not regexes and not args.pid:
        args.match = ["ros2_info_node"]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = out_dir / f"ros2_overhead_{stamp}.csv"
    png_path = out_dir / f"ros2_overhead_{stamp}.png"

    iface = choose_iface(args.iface)
    selector_parts = []
    if args.pid:
        selector_parts.append(f"pids={args.pid}")
    if args.match:
        selector_parts.append(f"match={args.match!r}")
    if args.match_regex:
        selector_parts.append(f"match_regex={args.match_regex!r}")
    print(f"Tracing node={args.node} {' '.join(selector_parts)} iface={iface}")
    print(f"Writing CSV to {csv_path}")

    fields = [
        "time_unix",
        "elapsed_s",
        "pids",
        "proc_count",
        "proc_cpu_percent",
        "proc_rss_mb",
        "net_iface",
        "net_rx_kib_s",
        "net_tx_kib_s",
        "ros_node_count",
        "ros_topic_count",
        "node_publishers",
        "node_subscribers",
        "node_service_servers",
        "node_service_clients",
        "node_action_servers",
        "node_action_clients",
    ]

    prev_cpu_total = read_total_cpu_ticks()
    prev_proc = {
        p.pid: p
        for p in find_matching_processes(args.match, regexes, set(args.pid), os.getpid())
    }
    prev_net = read_net_dev()[iface]
    prev_time = time.monotonic()
    start = prev_time

    ros_metrics = {
        "ros_node_count": -1,
        "ros_topic_count": -1,
        "node_publishers": -1,
        "node_subscribers": -1,
        "node_service_servers": -1,
        "node_service_clients": -1,
        "node_action_servers": -1,
        "node_action_clients": -1,
    }
    next_ros_sample = 0.0

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        while True:
            now = time.monotonic()
            elapsed = now - start
            if elapsed > args.duration:
                break

            time.sleep(max(0.0, args.interval - (time.monotonic() - now)))
            sample_time = time.monotonic()
            dt = max(sample_time - prev_time, 1e-9)

            cpu_total = read_total_cpu_ticks()
            proc_samples = {
                p.pid: p
                for p in find_matching_processes(args.match, regexes, set(args.pid), os.getpid())
            }
            proc_delta = 0
            for pid, sample in proc_samples.items():
                old = prev_proc.get(pid)
                if old:
                    proc_delta += max(0, sample.cpu_ticks - old.cpu_ticks)
            total_delta = max(1, cpu_total - prev_cpu_total)
            proc_cpu_percent = 100.0 * proc_delta / total_delta * (os.cpu_count() or 1)
            proc_rss_mb = sum(p.rss_bytes for p in proc_samples.values()) / (1024 * 1024)

            net_rx, net_tx = read_net_dev()[iface]
            prev_rx, prev_tx = prev_net
            net_rx_kib_s = max(0, net_rx - prev_rx) / 1024.0 / dt
            net_tx_kib_s = max(0, net_tx - prev_tx) / 1024.0 / dt

            if sample_time >= next_ros_sample:
                node_info = run_ros2(["node", "info", args.node], timeout=max(2.0, args.ros_interval * 0.8))
                ros_metrics.update(parse_node_info(node_info))
                ros_metrics["ros_node_count"] = ros_count(["node", "list"], timeout=2.0)
                ros_metrics["ros_topic_count"] = ros_count(["topic", "list"], timeout=2.0)
                next_ros_sample = sample_time + args.ros_interval

            row = {
                "time_unix": f"{time.time():.3f}",
                "elapsed_s": f"{sample_time - start:.3f}",
                "pids": " ".join(str(pid) for pid in sorted(proc_samples)),
                "proc_count": len(proc_samples),
                "proc_cpu_percent": f"{proc_cpu_percent:.3f}",
                "proc_rss_mb": f"{proc_rss_mb:.3f}",
                "net_iface": iface,
                "net_rx_kib_s": f"{net_rx_kib_s:.3f}",
                "net_tx_kib_s": f"{net_tx_kib_s:.3f}",
                **ros_metrics,
            }
            writer.writerow(row)
            f.flush()

            print(
                f"t={row['elapsed_s']}s pids=[{row['pids']}] "
                f"cpu={row['proc_cpu_percent']}% rss={row['proc_rss_mb']}MiB "
                f"rx={row['net_rx_kib_s']}KiB/s tx={row['net_tx_kib_s']}KiB/s"
            )

            prev_cpu_total = cpu_total
            prev_proc = proc_samples
            prev_net = (net_rx, net_tx)
            prev_time = sample_time

    if not args.no_plot:
        if write_plot(csv_path, png_path):
            print(f"Wrote plot to {png_path}")
        else:
            print("matplotlib is not installed, so only the CSV was written.")
            print("Install it with: python3 -m pip install matplotlib")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
