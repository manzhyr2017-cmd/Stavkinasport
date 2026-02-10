"""
=============================================================================
 V2.4 — WEB DASHBOARD (FastAPI Backend)

 Endpoints:
   GET  /api/signals         — Текущие сигналы
   GET  /api/expresses       — Текущие экспрессы
   GET  /api/bankroll        — Статистика банкролла
   GET  /api/bankroll/chart  — График банкролла за 30 дней
   GET  /api/scan            — Запустить сканирование
   GET  /api/model/metrics   — Метрики ML модели
   GET  /api/live/{match_id} — Live odds для матча
   POST /api/bet/settle      — Закрыть ставку (won/lost)
   GET  /api/health          — Health check
   WS   /ws/live             — WebSocket live updates

 Запуск:
   uvicorn dashboard.app:app --host 0.0.0.0 --port 8000 --reload
=============================================================================
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy imports — не падаем если не установлено
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI not installed: pip install fastapi uvicorn")


# ═══════════════════════════════════════════════════════════
#  PYDANTIC MODELS (API schemas)
# ═══════════════════════════════════════════════════════════

if HAS_FASTAPI:

    class SignalResponse(BaseModel):
        id: str
        match: str
        league: str
        outcome: str
        odds: float
        bookmaker: str
        probability: float
        edge: float
        confidence: str
        stake: float
        status: str
        created_at: str

    class ExpressResponse(BaseModel):
        id: str
        legs: int
        total_odds: float
        probability: float
        ev: float
        correlation_discount: float
        stake: float
        potential_win: float

    class BankrollResponse(BaseModel):
        bankroll: float
        initial: float
        peak: float
        drawdown: float
        total_bets: int
        win_rate: float
        roi: float
        daily_pnl: float
        weekly_pnl: float
        is_stopped: bool
        losing_streak: int
        kelly_fraction: float

    class SettleBetRequest(BaseModel):
        signal_id: str
        result: str  # "won" | "lost" | "void"

    class ScanResponse(BaseModel):
        matches_scanned: int
        singles: int
        expresses: int
        systems: int
        timestamp: str


# ═══════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════

def create_app(signal_generator=None, bankroll_manager=None) -> "FastAPI":
    """
    Factory для FastAPI приложения.
    
    Вызывается из main.py:
        app = create_app(generator, bankroll)
        uvicorn.run(app, host="0.0.0.0", port=8000)
    """
    if not HAS_FASTAPI:
        raise ImportError("FastAPI not installed")

    app = FastAPI(
        title="Betting Assistant V2 Dashboard",
        version="2.4",
        description="AI-powered sports betting analytics",
    )

    # CORS (разрешаем React frontend)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # В продакшене: конкретный домен
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State
    state = {
        "generator": signal_generator,
        "bankroll": bankroll_manager,
        "last_scan": None,
        "signals": [],
        "expresses": [],
        "systems": [],
        "ws_clients": set(),
        "bankroll_history": [],
    }

    # ─── ENDPOINTS ───

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "version": "2.4",
            "timestamp": datetime.utcnow().isoformat(),
            "bankroll_active": state["bankroll"] is not None,
        }

    @app.get("/api/signals", response_model=List[SignalResponse])
    async def get_signals():
        return [
            SignalResponse(
                id=s.id,
                match=(f"{s.match.home_team} vs {s.match.away_team}"
                       if s.match else "N/A"),
                league=s.match.league if s.match else "",
                outcome=s.outcome.value,
                odds=s.bookmaker_odds,
                bookmaker=s.bookmaker_name,
                probability=s.model_probability,
                edge=s.edge,
                confidence=s.confidence_level.value,
                stake=s.stake_amount,
                status=s.status.value,
                created_at=s.created_at.isoformat(),
            )
            for s in state.get("signals", [])
        ]

    @app.get("/api/expresses", response_model=List[ExpressResponse])
    async def get_expresses():
        return [
            ExpressResponse(
                id=e.id,
                legs=len(e.legs),
                total_odds=round(e.total_odds, 2),
                probability=round(e.combined_probability, 4),
                ev=round(e.adjusted_ev, 4),
                correlation_discount=e.correlation_discount,
                stake=e.stake_amount,
                potential_win=round(e.potential_win, 2),
            )
            for e in state.get("expresses", [])
        ]

    @app.get("/api/bankroll", response_model=BankrollResponse)
    async def get_bankroll():
        bm = state.get("bankroll")
        if not bm:
            raise HTTPException(404, "Bankroll manager not initialized")
        stats = bm.get_stats()
        return BankrollResponse(**stats)

    @app.get("/api/bankroll/chart")
    async def bankroll_chart():
        """График изменения банкролла (для Chart.js / Recharts)"""
        history = state.get("bankroll_history", [])
        return {
            "labels": [h["date"] for h in history],
            "data": [h["bankroll"] for h in history],
            "pnl": [h.get("pnl", 0) for h in history],
        }

    @app.get("/api/scan", response_model=ScanResponse)
    async def trigger_scan():
        gen = state.get("generator")
        if not gen:
            raise HTTPException(503, "Signal generator not ready")

        result = await gen.run_scan()
        state["signals"] = result.get("singles", [])
        state["expresses"] = result.get("expresses", [])
        state["systems"] = result.get("systems", [])
        state["last_scan"] = datetime.utcnow()

        # Record bankroll history
        bm = state.get("bankroll")
        if bm:
            state["bankroll_history"].append({
                "date": datetime.utcnow().isoformat(),
                "bankroll": bm.bankroll,
                "pnl": bm._daily_pnl,
            })

        # Broadcast to WebSocket clients
        await broadcast_ws(state, {
            "type": "scan_complete",
            "signals": len(state["signals"]),
            "expresses": len(state["expresses"]),
        })

        return ScanResponse(
            matches_scanned=result.get("matches_scanned", 0),
            singles=len(state["signals"]),
            expresses=len(state["expresses"]),
            systems=len(state.get("systems", [])),
            timestamp=datetime.utcnow().isoformat(),
        )

    @app.post("/api/bet/settle")
    async def settle_bet(req: SettleBetRequest):
        bm = state.get("bankroll")
        if not bm:
            raise HTTPException(404, "Bankroll not initialized")
        bm.settle_bet(req.signal_id, req.result)
        return {"status": "settled", "signal_id": req.signal_id,
                "result": req.result}

    @app.get("/api/model/metrics")
    async def model_metrics():
        """Метрики последнего обучения CatBoost"""
        return {
            "note": "Train model first with: python -m core.ml_pipeline",
            "placeholder": True,
        }

    # ─── WEBSOCKET ───

    @app.websocket("/ws/live")
    async def websocket_endpoint(websocket: WebSocket):
        await websocket.accept()
        state["ws_clients"].add(websocket)
        try:
            while True:
                data = await websocket.receive_text()
                # Клиент может подписаться на конкретные матчи
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            state["ws_clients"].discard(websocket)

    return app


async def broadcast_ws(state: dict, message: dict):
    """Отправить сообщение всем WS клиентам"""
    dead = set()
    for ws in state.get("ws_clients", set()):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    state["ws_clients"] -= dead


# ═══════════════════════════════════════════════════════════
#  REACT FRONTEND TEMPLATE (Single File)
# ═══════════════════════════════════════════════════════════

REACT_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Betting Assistant V2 Dashboard</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body class="bg-gray-900 text-white">
  <div id="root"></div>
  <script type="text/babel">
    const { useState, useEffect } = React;
    const API = window.location.origin + '/api';

    function App() {
      const [bankroll, setBankroll] = useState(null);
      const [signals, setSignals] = useState([]);
      const [expresses, setExpresses] = useState([]);
      const [scanning, setScanning] = useState(false);

      const fetchData = async () => {
        try {
          const [bRes, sRes, eRes] = await Promise.all([
            fetch(API + '/bankroll').then(r => r.json()),
            fetch(API + '/signals').then(r => r.json()),
            fetch(API + '/expresses').then(r => r.json()),
          ]);
          setBankroll(bRes);
          setSignals(sRes);
          setExpresses(eRes);
        } catch(e) { console.error(e); }
      };

      const triggerScan = async () => {
        setScanning(true);
        try {
          await fetch(API + '/scan');
          await fetchData();
        } catch(e) { console.error(e); }
        setScanning(false);
      };

      useEffect(() => { fetchData(); const i = setInterval(fetchData, 30000); return () => clearInterval(i); }, []);

      return (
        <div className="max-w-7xl mx-auto p-6">
          <h1 className="text-3xl font-bold mb-6">🤖 Betting Assistant V2</h1>
          
          {/* Bankroll Card */}
          {bankroll && (
            <div className="grid grid-cols-4 gap-4 mb-6">
              <div className="bg-gray-800 rounded-xl p-4">
                <div className="text-gray-400 text-sm">Bankroll</div>
                <div className="text-2xl font-bold text-green-400">${bankroll.bankroll}</div>
              </div>
              <div className="bg-gray-800 rounded-xl p-4">
                <div className="text-gray-400 text-sm">ROI</div>
                <div className={`text-2xl font-bold ${bankroll.roi >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {(bankroll.roi * 100).toFixed(1)}%
                </div>
              </div>
              <div className="bg-gray-800 rounded-xl p-4">
                <div className="text-gray-400 text-sm">Win Rate</div>
                <div className="text-2xl font-bold">{(bankroll.win_rate * 100).toFixed(0)}%</div>
              </div>
              <div className="bg-gray-800 rounded-xl p-4">
                <div className="text-gray-400 text-sm">Kelly</div>
                <div className="text-2xl font-bold">{(bankroll.kelly_fraction * 100).toFixed(0)}%</div>
                {bankroll.losing_streak >= 3 && <span className="text-red-400 text-sm">🔥 {bankroll.losing_streak} streak</span>}
              </div>
            </div>
          )}

          {/* Scan Button */}
          <button onClick={triggerScan} disabled={scanning}
            className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-3 rounded-lg mb-6 disabled:opacity-50">
            {scanning ? '⏳ Scanning...' : '🔍 Scan Market'}
          </button>

          {/* Signals Table */}
          <h2 className="text-xl font-bold mb-3">🎯 Value Bets ({signals.length})</h2>
          <div className="overflow-x-auto mb-6">
            <table className="w-full bg-gray-800 rounded-xl">
              <thead><tr className="text-gray-400 text-sm">
                <th className="p-3 text-left">Match</th>
                <th className="p-3">Outcome</th>
                <th className="p-3">Odds</th>
                <th className="p-3">Prob</th>
                <th className="p-3">Edge</th>
                <th className="p-3">Confidence</th>
                <th className="p-3">Stake</th>
              </tr></thead>
              <tbody>
                {signals.map(s => (
                  <tr key={s.id} className="border-t border-gray-700 hover:bg-gray-750">
                    <td className="p-3">{s.match}</td>
                    <td className="p-3 text-center font-bold">{s.outcome.toUpperCase()}</td>
                    <td className="p-3 text-center">{s.odds.toFixed(2)}</td>
                    <td className="p-3 text-center">{(s.probability * 100).toFixed(0)}%</td>
                    <td className="p-3 text-center text-green-400">+{(s.edge * 100).toFixed(1)}%</td>
                    <td className="p-3 text-center">{s.confidence}</td>
                    <td className="p-3 text-center font-bold">${s.stake.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Expresses */}
          <h2 className="text-xl font-bold mb-3">🔥 Expresses ({expresses.length})</h2>
          <div className="grid grid-cols-3 gap-4">
            {expresses.map(e => (
              <div key={e.id} className="bg-gray-800 rounded-xl p-4">
                <div className="text-lg font-bold">{e.legs} legs @ {e.total_odds.toFixed(2)}</div>
                <div className="text-sm text-gray-400">P: {(e.probability*100).toFixed(1)}% | EV: {(e.ev*100).toFixed(1)}%</div>
                {e.correlation_discount < 1 && <div className="text-xs text-yellow-400">⚠️ Corr: {(e.correlation_discount*100).toFixed(0)}%</div>}
                <div className="mt-2 text-green-400 font-bold">${e.stake.toFixed(2)} → ${e.potential_win.toFixed(2)}</div>
              </div>
            ))}
          </div>
        </div>
      );
    }

    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  </script>
</body>
</html>
"""


def write_dashboard_html(path: str = "dashboard/index.html"):
    """Создать файл React dashboard"""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(REACT_DASHBOARD_HTML)
    logger.info(f"Dashboard HTML written to {path}")
