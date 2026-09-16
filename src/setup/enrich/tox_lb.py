"""
tox_lb.py — ToxinPred3 本地 4-worker 字节级 TCP 负载均衡器。

不做 HTTP 解析。纯字节转发:
- 监听 127.0.0.1:8003
- round-robin 选 worker (8013/8023/8033/8043)
- 把客户端发来的所有字节原样写到 worker,把 worker 回的字节原样写回客户端
- 直到任一端 close

依赖: Python 3.12+ (用了 asyncio.StreamReader 协议)
"""
from __future__ import annotations
import asyncio
import itertools
import logging

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8003
WORKER_PORTS = [8013, 8023, 8033, 8043, 8053, 8063, 8073, 8083]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s LB %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tox_lb")

_counter = itertools.count()


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """字节级 forward。任一端 EOF 就 break。"""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("pipe err: %s", e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    rid = next(_counter)
    wport = WORKER_PORTS[rid % len(WORKER_PORTS)]
    peername = client_writer.get_extra_info("peername")
    try:
        # 等客户端先发(否则我们打开 worker 但没人写,worker 60s 后会关)
        # 实际上 httpx connect 后会立即发,这里 race-free 取决于客户端行为
        # 用 wait_for 给 30s 缓冲
        worker_reader, writer = await asyncio.open_connection("127.0.0.1", wport)
    except Exception as e:
        log.warning("rid=%d connect worker %d fail: %s", rid, wport, e)
        try:
            client_writer.close()
        except Exception:
            pass
        return

    log.debug("rid=%d from %s -> worker %d", rid, peername, wport)
    # 两个方向并发转发
    await asyncio.gather(
        _pipe(client_reader, writer),
        _pipe(worker_reader, client_writer),
    )
    try:
        client_writer.close()
    except Exception:
        pass


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT, backlog=128)
    log.info("LB listening on %s:%d -> workers %s", LISTEN_HOST, LISTEN_PORT, WORKER_PORTS)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
