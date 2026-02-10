import asyncio
import logging
import sys
import os

# Add root directory to path for imports
sys.path.append(os.getcwd())

from core.fonbet_health import FonbetEndpointManager

async def test_health():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    print("🚀 Testing Fonbet Endpoints...")
    
    manager = FonbetEndpointManager()
    try:
        results = await manager.check_all()
        
        print("\n" + "="*50)
        print(f"{'URL':<30} | {'Lat':<7} | {'Status':<10}")
        print("-" * 50)
        for s in results:
            icon = "✅" if s.alive else "❌"
            lat = f"{s.latency_ms}ms" if s.alive else "---"
            status = "Alive" if s.alive else f"Error: {s.error}"
            print(f"{s.url:<30} | {lat:<7} | {status}")
        
        best = await manager.get_active_endpoint()
        print("="*50)
        print(f"\n🏆 Best Endpoint: {best}")
        
    finally:
        await manager.close()

if __name__ == "__main__":
    asyncio.run(test_health())
