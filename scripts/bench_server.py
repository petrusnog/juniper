"""Mede o que importa para escolher a VPS da Juniper (T-081). Só biblioteca padrão.

    python3 bench_server.py                       # CPU, disco, memória e rede
    python3 bench_server.py --rounds 5 --out resultado.json
    JUNIPER_DEEPINFRA_API_KEY=... python3 bench_server.py --llm   # + uma chamada real ao LLM
    python3 bench_server.py --stack               # + o uso de memória dos contêineres

Roda NA VPS, não na sua máquina. Rode mais de uma vez, em horários diferentes: numa VPS
compartilhada o que pesa é a variação, não o melhor resultado. A chave do LLM só vem do ambiente
(nunca por argumento) e nunca é impressa nem gravada no resultado.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import shutil
import socket
import ssl
import statistics
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

HOSTS = ("api.deepinfra.com", "api.groq.com")
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"


# --- utilidades ---------------------------------------------------------------------------


def _pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 2),
        "median": round(statistics.median(values), 2),
        "p95": round(_pct(values, 95), 2),
        "max": round(max(values), 2),
    }


def _spread(values: list[float]) -> float:
    """Variação relativa entre rodadas (desvio/média, em %)."""
    mean = statistics.mean(values)
    return round(100 * statistics.pstdev(values) / mean, 1) if mean else 0.0


# --- sistema --------------------------------------------------------------------------------


def system_info() -> dict[str, Any]:
    mem: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            mem[key] = int(rest.split()[0])  # kB
    model = ""
    with open("/proc/cpuinfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("model name"):
                model = line.partition(":")[2].strip()
                break
    disk = shutil.disk_usage("/")
    return {
        "os": platform.platform(),
        "cpu_model": model,
        "cpus": os.cpu_count(),
        "mem_total_mb": mem["MemTotal"] // 1024,
        "mem_available_mb": mem["MemAvailable"] // 1024,
        "swap_total_mb": mem.get("SwapTotal", 0) // 1024,
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_free_gb": round(disk.free / 1e9, 1),
    }


# --- CPU: velocidade e *steal* (tempo roubado pelos vizinhos) -------------------------------


def _cpu_times() -> tuple[int, int]:
    """(total, steal) dos jiffies da CPU desde o boot."""
    with open("/proc/stat", encoding="utf-8") as handle:
        fields = [int(x) for x in handle.readline().split()[1:]]
    return sum(fields), (fields[7] if len(fields) > 7 else 0)


def _burn(seconds: float) -> int:
    end = time.perf_counter() + seconds
    count = 0
    while time.perf_counter() < end:
        for _ in range(20_000):
            count += 1
    return count


def bench_cpu(rounds: int, seconds: float = 8.0) -> dict[str, Any]:
    """Cada rodada ocupa todos os núcleos por `seconds`. Mede o trabalho feito e o steal."""
    cpus = os.cpu_count() or 1
    work: list[float] = []
    steals: list[float] = []
    with multiprocessing.Pool(cpus) as pool:
        for _ in range(rounds):
            total0, steal0 = _cpu_times()
            counts = pool.map(_burn, [seconds] * cpus)
            total1, steal1 = _cpu_times()
            work.append(sum(counts) / seconds / 1e6)  # milhões de iterações por segundo
            steals.append(100 * (steal1 - steal0) / max(1, total1 - total0))
    return {
        "mops_per_round": [round(x, 1) for x in work],
        "mops_variation_pct": _spread(work),
        "steal_pct_per_round": [round(x, 2) for x in steals],
        "steal_pct_max": round(max(steals), 2),
    }


# --- disco: o Mongo vive de fsync -------------------------------------------------------------


def bench_disk(megabytes: int = 256) -> dict[str, Any]:
    block = os.urandom(1024 * 1024)
    with tempfile.NamedTemporaryFile(dir=os.path.expanduser("~"), delete=False) as handle:
        path = handle.name
    try:
        start = time.perf_counter()
        with open(path, "wb") as out:
            for _ in range(megabytes):
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        write_mbs = megabytes / (time.perf_counter() - start)

        fsyncs: list[float] = []
        with open(path, "r+b") as out:
            for i in range(200):
                out.seek(i * 4096)
                out.write(b"x" * 4096)
                tick = time.perf_counter()
                out.flush()
                os.fsync(out.fileno())
                fsyncs.append((time.perf_counter() - tick) * 1000)
        return {
            "write_mb_s": round(write_mbs, 1),
            "fsync_ms": _summary(fsyncs),
        }
    finally:
        os.unlink(path)


# --- rede: o trajeto até os LLMs -------------------------------------------------------------


def _tls_get(host: str) -> dict[str, float]:
    context = ssl.create_default_context()
    t0 = time.perf_counter()
    sock = socket.create_connection((host, 443), timeout=10)
    t1 = time.perf_counter()
    tls = context.wrap_socket(sock, server_hostname=host)
    t2 = time.perf_counter()
    tls.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
    tls.recv(1)
    t3 = time.perf_counter()
    tls.close()
    return {
        "tcp_ms": (t1 - t0) * 1000,
        "tls_ms": (t2 - t1) * 1000,
        "first_byte_ms": (t3 - t2) * 1000,
    }


def bench_network(samples: int = 15) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for host in HOSTS:
        runs: list[dict[str, float]] = []
        error = ""
        for _ in range(samples):
            try:
                runs.append(_tls_get(host))
            except OSError as exc:
                error = type(exc).__name__
        if runs:
            result[host] = {
                # o `tcp_ms` é, na prática, o RTT até o servidor
                "tcp_rtt_ms": _summary([r["tcp_ms"] for r in runs]),
                "first_byte_ms": _summary([r["first_byte_ms"] for r in runs]),
                "ok": len(runs),
                "failed": samples - len(runs),
            }
        else:
            result[host] = {"error": error or "sem resposta"}
    return result


def bench_llm(model: str) -> dict[str, Any]:
    """Uma conversa curta e real (a chave vem só do ambiente)."""
    key = os.environ.get("JUNIPER_DEEPINFRA_API_KEY", "")
    if not key:
        return {"skipped": "defina JUNIPER_DEEPINFRA_API_KEY no ambiente"}
    body = json.dumps(
        {
            "model": model,
            "stream": True,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "Responda só: ok."}],
        }
    ).encode()
    request = urllib.request.Request(
        DEEPINFRA_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # o "Python-urllib" padrão é barrado por serviços atrás de uma CDN (HTTP 403)
            "User-Agent": "juniper-bench/1.0",
        },
    )
    samples: list[dict[str, float]] = []
    for _ in range(5):
        start = time.perf_counter()
        first: float | None = None
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                for line in response:
                    if first is None and line.startswith(b"data:") and b"[DONE]" not in line:
                        first = (time.perf_counter() - start) * 1000
        except urllib.error.HTTPError as exc:
            # o corpo da resposta nunca leva a chave; mostra o motivo sem expô-la
            detail = exc.read(300).decode("utf-8", "replace").replace(key, "***")
            return {"error": f"HTTP {exc.code}", "detail": detail}
        except urllib.error.URLError as exc:
            return {"error": type(exc).__name__, "detail": str(exc.reason)[:200]}
        samples.append(
            {"first_token_ms": first or 0.0, "total_ms": (time.perf_counter() - start) * 1000}
        )
    return {
        "model": model,
        "first_token_ms": _summary([s["first_token_ms"] for s in samples]),
        "total_ms": _summary([s["total_ms"] for s in samples]),
    }


# --- pilha Docker (brain + mongo) -------------------------------------------------------------


def bench_stack() -> dict[str, Any]:
    if shutil.which("docker") is None:
        return {"skipped": "docker não instalado"}
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}};{{.MemUsage}};{{.CPUPerc}}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    rows = [line.split(";") for line in out.stdout.strip().splitlines() if line]
    return {"containers": [{"name": r[0], "mem": r[1], "cpu": r[2]} for r in rows if len(r) == 3]}


# --- relatório --------------------------------------------------------------------------------


def report(data: dict[str, Any]) -> str:
    sysinfo = data["system"]
    lines = [
        "== Sistema ==",
        f"  {sysinfo['cpus']} vCPU ({sysinfo['cpu_model']}), {sysinfo['mem_total_mb']} MB de RAM "
        f"({sysinfo['mem_available_mb']} MB livres agora), swap {sysinfo['swap_total_mb']} MB",
        f"  disco: {sysinfo['disk_free_gb']} GB livres de {sysinfo['disk_total_gb']} GB",
    ]
    if "cpu" in data:
        cpu = data["cpu"]
        lines += [
            "== CPU ==",
            f"  Mops/s por rodada: {cpu['mops_per_round']} (variação {cpu['mops_variation_pct']}%)",
            f"  steal máximo: {cpu['steal_pct_max']}% (acima de ~5% = vizinhos atrapalhando)",
        ]
    if "disk" in data:
        disk = data["disk"]
        lines += [
            "== Disco ==",
            f"  escrita: {disk['write_mb_s']} MB/s; fsync (ms): {disk['fsync_ms']}",
        ]
    if "network" in data:
        lines.append("== Rede (RTT = tempo de conexão TCP) ==")
        for host, info in data["network"].items():
            if "error" in info:
                lines.append(f"  {host}: {info['error']}")
            else:
                lines.append(
                    f"  {host}: RTT {info['tcp_rtt_ms']} ({info['ok']} ok, {info['failed']} falhas)"
                )
    if "llm" in data:
        lines += ["== LLM (DeepInfra, resposta curta com stream) ==", f"  {data['llm']}"]
    if "stack" in data:
        lines += ["== Contêineres ==", f"  {data['stack']}"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--rounds", type=int, default=3, help="rodadas de CPU (padrão: 3)")
    parser.add_argument("--llm", action="store_true", help="faz chamadas reais ao DeepInfra")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--stack", action="store_true", help="mede os contêineres em execução")
    parser.add_argument("--skip-cpu", action="store_true")
    parser.add_argument("--out", help="grava o resultado em JSON")
    args = parser.parse_args()

    data: dict[str, Any] = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "system": system_info(),
    }
    if not args.skip_cpu:
        print("CPU (cada rodada leva ~8 s)...", flush=True)
        data["cpu"] = bench_cpu(args.rounds)
    print("Disco...", flush=True)
    data["disk"] = bench_disk()
    print("Rede...", flush=True)
    data["network"] = bench_network()
    if args.llm:
        print("LLM...", flush=True)
        data["llm"] = bench_llm(args.model)
    if args.stack:
        data["stack"] = bench_stack()

    print()
    print(report(data))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        print(f"\nResultado em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
