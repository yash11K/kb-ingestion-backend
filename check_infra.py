"""Quick diagnostic: test DB connectivity, pool health, and idle timeout."""

import asyncio
import os
import time

import asyncpg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


async def main():
    print("=" * 60)
    print("DB CONNECTION DIAGNOSTIC")
    print("=" * 60)

    # 1. Basic connectivity
    print("\n[1] Basic connect... ", end="", flush=True)
    t0 = time.perf_counter()
    try:
        conn = await asyncpg.connect(DATABASE_URL, ssl="require", timeout=10)
        latency = (time.perf_counter() - t0) * 1000
        version = await conn.fetchval("SELECT version()")
        print(f"OK ({latency:.0f}ms)")
        print(f"    Server: {version[:80]}")
        await conn.close()
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        return

    # 2. Pool creation
    print("\n[2] Pool create (min=2, max=5)... ", end="", flush=True)
    try:
        pool = await asyncpg.create_pool(
            DATABASE_URL, ssl="require", min_size=2, max_size=5,
            statement_cache_size=0, command_timeout=10,
        )
        print(f"OK (size={pool.get_size()}, free={pool.get_idle_size()})")
    except Exception as e:
        print(f"FAILED: {e}")
        return

    # 3. Simple query through pool
    print("\n[3] Pool query... ", end="", flush=True)
    t0 = time.perf_counter()
    try:
        result = await pool.fetchval("SELECT 1")
        latency = (time.perf_counter() - t0) * 1000
        print(f"OK ({latency:.0f}ms) -> {result}")
    except Exception as e:
        print(f"FAILED: {e}")

    # 4. Idle timeout test — hold a connection idle, then reuse it
    IDLE_SECONDS = 15  # quick check first; bump to 35+ to test Neon's 30s limit
    print(f"\n[4] Idle timeout test ({IDLE_SECONDS}s wait)...")
    print("    Acquiring connection... ", end="", flush=True)
    try:
        conn = await pool.acquire()
        await conn.fetchval("SELECT 1")  # warm it up
        print("OK")
    except Exception as e:
        print(f"FAILED to acquire: {e}")
        await pool.close()
        return

    print(f"    Holding idle for {IDLE_SECONDS}s", end="", flush=True)
    for i in range(IDLE_SECONDS):
        await asyncio.sleep(1)
        if (i + 1) % 5 == 0:
            print(f" {i+1}s", end="", flush=True)
    print()

    print("    Reusing after idle... ", end="", flush=True)
    try:
        t0 = time.perf_counter()
        val = await conn.fetchval("SELECT 1")
        latency = (time.perf_counter() - t0) * 1000
        print(f"OK ({latency:.0f}ms) -> {val}")
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        print("    ^ Connection died after idle — this confirms the timeout issue.")

    try:
        await pool.release(conn)
    except Exception:
        pass

    await pool.close()
    print("\n" + "=" * 60)
    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
